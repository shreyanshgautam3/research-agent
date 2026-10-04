import json, sys

for line in open(sys.argv[1], encoding="utf-8"):
    row = json.loads(line)
    print("Q:", row["question"][:70])
    print("  steps:", row["steps"], "cost:", row["cost"], "compactions:", row["compactions"], "duplicates:", row["duplicates"])
    rep = row["report"]
    if rep:
        print("  SUMMARY:", rep["summary"])
        for f in rep["findings"]:
            print("  -", f["confidence"], f["source_urls"])
    print()