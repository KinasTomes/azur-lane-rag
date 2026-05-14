from src.graph_rag.graph_store import (
    DB_PATH,
    OUTPUT_DIR,
    apply_delta,
    create_graph_schema,
    delete_ship,
    graph_counts,
    init_graph_db,
    load_all_ship_files,
    upsert_ship_file,
)


def init_db(db_path=DB_PATH, force_rebuild=True):
    return init_graph_db(db_path, force_rebuild=force_rebuild)


def load_data(conn, output_dir=OUTPUT_DIR, ship_files=None):
    if ship_files is None:
        processed = load_all_ship_files(conn, output_dir)
    else:
        processed = 0
        for ship_file in ship_files:
            if upsert_ship_file(conn, ship_file):
                processed += 1

    node_count, edge_count = graph_counts(conn)
    print(f"Processed {processed} JSON files.")
    print(f"Import completed. Database saved to: {DB_PATH}")
    print(f"Nodes: {node_count}")
    print(f"Edges: {edge_count}")


if __name__ == "__main__":
    connection = init_db()
    load_data(connection)
    connection.close()
