# cli.py
import argparse, json
from engine import (
    load_ibge_full_best_effort, slice_ibge_by_rank,
    run_active_search, match_names
)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Target URL")
    ap.add_argument("--mode", default="active", choices=["active"])
    ap.add_argument("--limit_first", type=int, default=3000)
    ap.add_argument("--limit_surname", type=int, default=3000)
    ap.add_argument("--max_cycles", type=int, default=50, help="How many surnames to run")
    ap.add_argument("--selenium_wait", type=int, default=15)
    ap.add_argument("--post_submit_sleep", type=float, default=0.4)
    ap.add_argument("--headless", action="store_true", default=False)
    ap.add_argument("--try_people_tab_click", action="store_true", default=True)
    args = ap.parse_args()

    first_full, surname_full, meta, mode = load_ibge_full_best_effort(True, True)
    first_ranks, surname_ranks, sorted_surnames = slice_ibge_by_rank(
        first_full, surname_full, args.limit_first, args.limit_surname
    )

    surnames = sorted_surnames[: args.max_cycles]

    results = run_active_search(
        start_url=args.url,
        surnames=surnames,
        selenium_wait_s=args.selenium_wait,
        post_submit_sleep=args.post_submit_sleep,
        try_people_tab_click=args.try_people_tab_click,
        headless=args.headless,
    )

    all_matches = []
    seen = set()

    for surname, records in results:
        matches = match_names(
            records,
            source=f"Search: {surname}",
            first_name_ranks=first_ranks,
            surname_ranks=surname_ranks,
            limit_first=args.limit_first,
            limit_surname=args.limit_surname,
            allow_surname_only=True,
            block_mit_word=False,
        )
        for m in matches:
            key = (m["Full Name"], (m.get("Email") or "").lower())
            if key in seen:
                continue
            seen.add(key)
            all_matches.append(m)

        print(f"{surname}: candidates={len(records)} matches={len(matches)}")

    all_matches.sort(key=lambda x: x.get("Brazil Score", 0), reverse=True)

    print("\n=== TOP RESULTS ===")
    print(json.dumps(all_matches[:50], ensure_ascii=False, indent=2))

    with open("results.json", "w", encoding="utf-8") as f:
        json.dump(all_matches, f, ensure_ascii=False, indent=2)

    print("\nSaved: results.json")

if __name__ == "__main__":
    main()
