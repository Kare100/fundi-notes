"""Command line helpers.  Run from the backend folder:

    python -m app.cli init-db        create the database tables
    python -m app.cli seed           load the sample dataset (demo reports, processed by the configured LLM)
    python -m app.cli seed --reset   delete all existing reports first
    python -m app.cli purge-audio    delete retained audio past its retention date
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import delete

from .config import get_settings, local_today
from .database import get_sessionmaker, init_db
from .models import Report
from .services import samples
from .services.pipeline import create_text_report, purge_expired_audio, run_processing


def seed(reset: bool) -> int:
    init_db()
    today = local_today()
    with get_sessionmaker()() as db:
        if reset:
            db.execute(delete(Report))
            db.commit()
        ids = []
        for s in samples.load_samples():
            report = create_text_report(
                db,
                text=s["transcript"],
                sample_id=s["id"],
                reported_on=today - timedelta(days=int(s.get("reported_offset_days", 0))),
            )
            ids.append(report.id)
    for report_id in ids:
        run_processing(report_id, None)
    return len(ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--reset", action="store_true")
    sub.add_parser("purge-audio")
    args = parser.parse_args()

    get_settings()
    if args.cmd == "init-db":
        init_db()
        print("Database ready.")
    elif args.cmd == "seed":
        print(f"Created and processed {seed(args.reset)} sample reports.")
    elif args.cmd == "purge-audio":
        init_db()
        with get_sessionmaker()() as db:
            print(f"Purged {purge_expired_audio(db)} expired audio file(s).")


if __name__ == "__main__":
    main()
