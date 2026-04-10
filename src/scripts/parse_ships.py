import sys
import os
import json
import time
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.ship_parser import ShipDataParser
from utils.get_ship_summary import get_enhanced_summaries


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]


def collect_indexed_ship_ids(*directories: Path) -> set[int]:
    indexed_ids: set[int] = set()
    for directory in directories:
        if not directory.exists():
            continue
        for json_file in directory.glob("*.json"):
            stem = json_file.stem
            if stem.isdigit():
                indexed_ids.add(int(stem))
    return indexed_ids


def process_batch(parser: ShipDataParser, batch: List[Dict[str, Any]], output_dir: Path, error_log: Path, batch_num: int):
    """Xử lý một batch và lưu kết quả."""
    try:
        print(f"Processing batch {batch_num}...")
        parsed_ships = parser.parse_ship_summaries(batch)
        # Lưu từng tàu vào một file riêng
        for ship in parsed_ships:
            if isinstance(ship, dict) and "id" in ship:
                ship_id = ship["id"]
                ship_file = output_dir / f"{ship_id}.json"
                with open(ship_file, "w", encoding="utf-8") as f:
                    json.dump(ship, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        # Ghi log lỗi với thông tin các tàu trong batch
        with open(error_log, "a", encoding="utf-8") as f:
            ship_ids = [s.get("id", "Unknown") for s in batch]
            f.write(f"Error processing ships {ship_ids} in batch {batch_num}: {str(e)}\n")
        return False


def main(max_batches: Optional[int] = None, parallel: bool = False, workers: int = 5):
    api_key = os.environ.get("NVIDIA_API_KEY")
    if not api_key:
        print("Error: NVIDIA_API_KEY not set")
        sys.exit(1)

    # Ưu tiên output ở repo root, nhưng vẫn hỗ trợ legacy output trong src.
    output_dir = REPO_ROOT / "output"
    legacy_output_dir = SRC_ROOT / "output"

    output_dir.mkdir(exist_ok=True)

    root_ids = collect_indexed_ship_ids(output_dir)
    legacy_ids = collect_indexed_ship_ids(legacy_output_dir)

    if not root_ids and legacy_ids:
        output_dir = legacy_output_dir
        output_dir.mkdir(exist_ok=True)
        print(f"Detected existing indexed files in legacy output dir: {output_dir}")

    # File log lỗi
    error_log = output_dir / "error_log.txt"

    parser = ShipDataParser(api_key)
    summaries = get_enhanced_summaries()

    indexed_ship_ids = collect_indexed_ship_ids(output_dir, legacy_output_dir)

    pending_summaries = []
    for summary in summaries:
        ship_id = summary.get("id")
        if ship_id is None:
            pending_summaries.append(summary)
            continue

        if ship_id in indexed_ship_ids:
            continue

        pending_summaries.append(summary)

    print(f"Total ships: {len(summaries)} | Indexed: {len(indexed_ship_ids)} | Pending: {len(pending_summaries)}")

    # Chia batch
    batch_size = 5
    batches = [pending_summaries[i:i + batch_size] for i in range(0, len(pending_summaries), batch_size)]

    if max_batches is not None:
        batches = batches[:max_batches]
        print(f"Limiting to {max_batches} batches.")

    if not batches:
        print("No pending ships to process.")
        return

    if parallel:
        print(f"🚀 Running in PARALLEL mode with {workers} workers.")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_batch = {
                executor.submit(process_batch, parser, batch, output_dir, error_log, idx + 1): idx 
                for idx, batch in enumerate(batches)
            }
            for future in as_completed(future_to_batch):
                future.result() # Đợi hoàn thành
    else:
        print("🐢 Running in SEQUENTIAL mode.")
        for idx, batch in enumerate(batches):
            process_batch(parser, batch, output_dir, error_log, idx + 1)
            time.sleep(1) # Nghỉ ngắn giữa các batch ở chế độ tuần tự

    print("All batches processed.")

if __name__ == "__main__":
    argument_parser = argparse.ArgumentParser(description="Parse Azur Lane ships in batches.")
    argument_parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Limit how many batches to process for a test run. Omit to process all batches."
    )
    argument_parser.add_argument(
        "--parallel",
        action="store_true",
        help="Enable parallel processing of batches."
    )
    argument_parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of parallel workers (default: 5)."
    )
    arguments = argument_parser.parse_args()
    main(
        max_batches=arguments.max_batches,
        parallel=arguments.parallel,
        workers=arguments.workers
    )