"""
GraphRAG update orchestrator.

The graph structure is patched per changed ship. Community detection and level-1
structure are rebuilt globally because they are derived from the whole graph.
Expensive LLM summaries and embeddings are skipped by content hash.
"""

import argparse
import concurrent.futures
import logging
import sqlite3
import sys
from pathlib import Path

from src.graph_rag.delta import detect_delta
from src.graph_rag.graph_store import apply_delta, graph_counts, init_graph_db, load_all_ship_files


BASE_DIR = Path(__file__).resolve().parents[2]
GRAPH_DB_PATH = BASE_DIR / "data" / "azur_lane_graph.db"
OUTPUT_DIR = BASE_DIR / "output"
PHASES = ["delta", "graph", "community", "summarize0", "level1", "vectorize"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class GraphRAGUpdater:
    def __init__(self, force=False, dry_run=False, workers=5):
        self.force = force
        self.dry_run = dry_run
        self.workers = max(1, workers)

    def run(self):
        delta = self.phase_delta()
        had_graph_db = GRAPH_DB_PATH.exists()
        self.phase_graph(delta)
        should_rebuild_communities = (
            self.force
            or not had_graph_db
            or bool(delta["new"])
            or bool(delta["updated"])
        )
        ran_community = self.phase_community(should_run=should_rebuild_communities)
        self.phase_summarize0(should_run=ran_community)
        self.phase_level1(should_run=ran_community)
        self.phase_vectorize()
        logger.info("GraphRAG update complete.")

    def run_phase(self, phase):
        if phase == "delta":
            self.phase_delta()
            return

        if phase == "graph":
            self.phase_graph(self.phase_delta())
            return

        handlers = {
            "community": self.phase_community,
            "summarize0": self.phase_summarize0,
            "level1": self.phase_level1,
            "vectorize": self.phase_vectorize,
        }
        handler = handlers.get(phase)
        if handler is None:
            logger.error(f"Unknown phase: {phase}")
            sys.exit(1)
        handler()

    def phase_delta(self):
        logger.info("=== Phase 1: Delta detection ===")
        delta = detect_delta(OUTPUT_DIR, GRAPH_DB_PATH)
        logger.info(
            "Output files: %s, graph_meta rows: %s",
            len(delta["current"]),
            delta["stored_count"],
        )
        logger.info(
            "Delta: %s new, %s updated, %s deleted, %s unchanged",
            len(delta["new"]),
            len(delta["updated"]),
            len(delta["deleted"]),
            delta["unchanged"],
        )
        self._log_ship_preview("New", delta["new"], delta)
        self._log_ship_preview("Updated", delta["updated"], delta)
        if delta["deleted"]:
            logger.warning("Deleted: %s", ", ".join(delta["deleted"][:10]))
        return delta

    def phase_graph(self, delta):
        logger.info("=== Phase 2: Graph patch ===")
        has_graph_changes = any(delta[key] for key in ("new", "updated", "deleted"))

        if self.dry_run:
            if not GRAPH_DB_PATH.exists():
                logger.info("[DRY-RUN] Would build graph DB from all output JSON files.")
            else:
                logger.info(
                    "[DRY-RUN] Would patch %s new, %s updated, %s deleted ships.",
                    len(delta["new"]),
                    len(delta["updated"]),
                    len(delta["deleted"]),
                )
            return

        if not self.force and GRAPH_DB_PATH.exists() and not has_graph_changes:
            logger.info("Graph patch skipped: no ship output changes.")
            return

        if self.force:
            conn = init_graph_db(GRAPH_DB_PATH, force_rebuild=True)
            try:
                processed = load_all_ship_files(conn, OUTPUT_DIR)
                logger.info("Force rebuilt graph from %s ship files.", processed)
            finally:
                conn.close()
        else:
            apply_delta(GRAPH_DB_PATH, delta)

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            node_count, edge_count = graph_counts(conn)
            logger.info("Graph now has %s nodes and %s edges.", node_count, edge_count)
        finally:
            conn.close()

    def phase_community(self, should_run=True):
        logger.info("=== Phase 3: Community rebuild ===")
        if not GRAPH_DB_PATH.exists():
            logger.error("Graph DB not found. Run graph phase first.")
            return False
        if not should_run:
            logger.info("Community rebuild skipped: no new or updated ships.")
            return False
        if self.dry_run:
            logger.info("[DRY-RUN] Would run Leiden community detection on full graph.")
            return True

        from src.graph_rag.community_build_base import (
            run_community_detection,
            setup_db_for_communities,
        )

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            setup_db_for_communities(conn)
            run_community_detection(conn)
        finally:
            conn.close()
        return True

    def phase_summarize0(self, should_run=True):
        logger.info("=== Phase 4: Level-0 summaries ===")
        if not GRAPH_DB_PATH.exists():
            logger.error("Graph DB not found.")
            return
        if not should_run:
            logger.info("Level-0 summaries skipped: community assignments unchanged.")
            return

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM communities WHERE level = 0 ORDER BY id")
            community_ids = [row[0] for row in cursor.fetchall()]
            if self.dry_run:
                logger.info(
                    "[DRY-RUN] Would check %s level-0 communities and summarize changed ones with %s workers.",
                    len(community_ids),
                    self.workers,
                )
                return

            logger.info(
                "Running level-0 summarization with %s workers across %s communities.",
                self.workers,
                len(community_ids),
            )
            summarized = 0
            skipped = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                future_map = {
                    executor.submit(self._summarize_level0_community, community_id): community_id
                    for community_id in community_ids
                }
                for future in concurrent.futures.as_completed(future_map):
                    community_id = future_map[future]
                    try:
                        if future.result():
                            summarized += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        logger.exception(
                            "Level-0 community %s failed during summarization: %s",
                            community_id,
                            exc,
                        )
            logger.info("Level-0 summaries: %s updated, %s skipped.", summarized, skipped)
        finally:
            conn.close()

    def phase_level1(self, should_run=True):
        logger.info("=== Phase 5: Level-1 rebuild and summaries ===")
        if not GRAPH_DB_PATH.exists():
            logger.error("Graph DB not found.")
            return
        if not should_run:
            logger.info("Level-1 rebuild and summaries skipped: level-0 communities unchanged.")
            return

        if self.dry_run:
            conn = sqlite3.connect(GRAPH_DB_PATH)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM communities WHERE level = 0")
                level0_count = cursor.fetchone()[0]
                cursor.execute(
                    """
                    SELECT COUNT(*)
                    FROM edges e
                    JOIN nodes n1 ON n1.id = e.source_id
                    JOIN nodes n2 ON n2.id = e.target_id
                    WHERE n1.community_id IS NOT NULL
                      AND n2.community_id IS NOT NULL
                      AND n1.community_id != n2.community_id
                    """
                )
                meta_edge_count = cursor.fetchone()[0]
                logger.info(
                    "[DRY-RUN] Would rebuild level-1 from %s level-0 communities and %s inter-community edges, then summarize changed ones with %s workers.",
                    level0_count,
                    meta_edge_count,
                    self.workers,
                )
            finally:
                conn.close()
            return

        from src.graph_rag.community_build_level_1 import (
            ensure_level1_tables,
            get_level0_communities,
            get_meta_edges,
            run_level1_leiden,
            upsert_level1_structure,
        )

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            ensure_level1_tables(conn)
            level0_map = get_level0_communities(conn)
            if not level0_map:
                logger.warning("No level-0 communities found.")
                return

            level0_ids = sorted(level0_map.keys())
            meta_edges = get_meta_edges(conn)
            level1_groups = run_level1_leiden(level0_ids, meta_edges)
            upsert_level1_structure(conn, level1_groups)

            from src.graph_rag.community_summarize_level_1 import (
                get_level0_community_map,
                get_level1_children,
                summarize_level1_community,
            )

            level0_summary_map = get_level0_community_map(conn)
            parent_to_children = get_level1_children(conn)
            logger.info(
                "Running level-1 summarization with %s workers across %s communities.",
                self.workers,
                len(parent_to_children),
            )
            summarized = 0
            skipped = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as executor:
                future_map = {
                    executor.submit(
                        self._summarize_level1_community,
                        level1_id,
                        child_ids,
                        level0_summary_map,
                    ): level1_id
                    for level1_id, child_ids in parent_to_children.items()
                }
                for future in concurrent.futures.as_completed(future_map):
                    level1_id = future_map[future]
                    try:
                        if future.result():
                            summarized += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        logger.exception(
                            "Level-1 community %s failed during summarization: %s",
                            level1_id,
                            exc,
                        )
            logger.info("Level-1 summaries: %s updated, %s skipped.", summarized, skipped)
        finally:
            conn.close()

    def phase_vectorize(self):
        logger.info("=== Phase 6: Vectorization ===")
        if self.dry_run:
            logger.info("[DRY-RUN] Would vectorize changed communities, skills, and ships.")
            return

        from src.graph_rag.vectorize_all import AzurLaneVectorizer

        vectorizer = AzurLaneVectorizer(force=self.force)
        vectorizer.vectorize_communities()
        vectorizer.vectorize_skills()
        vectorizer.vectorize_ships_basic()

    @staticmethod
    def _log_ship_preview(label, ship_ids, delta):
        if not ship_ids:
            return
        items = []
        for ship_id in ship_ids[:10]:
            name = delta["current"].get(ship_id, {}).get("name", "?")
            items.append(f"{ship_id} ({name})")
        suffix = f" ... +{len(ship_ids) - 10} more" if len(ship_ids) > 10 else ""
        logger.info("%s: %s%s", label, ", ".join(items), suffix)

    def _summarize_level0_community(self, community_id):
        from src.graph_rag.community_summarize_base import summarize_community

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            return summarize_community(conn, community_id, force=self.force)
        finally:
            conn.close()

    def _summarize_level1_community(self, level1_id, child_ids, level0_summary_map):
        from src.graph_rag.community_summarize_level_1 import summarize_level1_community

        conn = sqlite3.connect(GRAPH_DB_PATH)
        try:
            return summarize_level1_community(
                conn,
                level1_id,
                child_ids,
                level0_summary_map,
                force=self.force,
            )
        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(description="GraphRAG update pipeline")
    parser.add_argument("--force", action="store_true", help="Reprocess all graph, summaries, and vectors")
    parser.add_argument("--dry-run", action="store_true", help="Print planned work without mutating data")
    parser.add_argument("--phase", choices=PHASES, help="Run a single pipeline phase")
    parser.add_argument("--workers", type=int, default=5, help="Worker count for level-0 community summarization")
    args = parser.parse_args()

    updater = GraphRAGUpdater(force=args.force, dry_run=args.dry_run, workers=args.workers)
    if args.phase:
        updater.run_phase(args.phase)
    else:
        updater.run()


if __name__ == "__main__":
    main()
