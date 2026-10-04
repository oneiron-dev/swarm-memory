# swarm-reader runbook

`W` is the work area outside the repo, `R` this repo, `PA` the prime-agent path. See SKILL.md for what each step does.

```bash
python $R/pipeline/slice/slicer.py --data <archive> --out $W                 # 1 slice; humans pooled here
<your tagger> $W/slice → $W/tags                                             # 2 tag; contract in pipeline/tag/README.md
python $R/pipeline/summarize/summarize.py estimate --work $W                 # 3 size the summary run
python $R/pipeline/summarize/summarize.py run --work $W --prime-agent $PA    # 4 cited summaries, name guard on
python $R/pipeline/summarize/summarize.py audit-existing --work $W           # 5 name check over older records
python $R/pipeline/bundle/human_names.py                                     # 6 human name list (never served)
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 7 first bundle
python $R/pipeline/bundle/human_ids.py candidates --data $W/bundle/data && \
  python $R/pipeline/bundle/human_ids.py classify --prime-agent $PA && \
  python $R/pipeline/bundle/human_ids.py scan && python $R/pipeline/bundle/human_ids.py hits   # 8 handle sweep
python $R/pipeline/bundle/human_ids.py write --review $W/human_ids/review.txt   # 9 a person reviews every hit
cd $R/pipeline/book && for s in days gaps chapters hooks pairs stage; do python book.py $s --parallel 8 --prime-agent $PA; done
python book.py adherence --parallel 4 --prime-agent $PA && python book.py rephrase --prime-agent $PA   # 10 goal calls, rewordings
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 11 full bundle, swapped only if valid
python $R/pipeline/bundle/human_ids.py redteam --leaks <leaks.jsonl> --review <file>        # 12 per red-team round, then rebuild
python $R/pipeline/pf/pf.py collect … run … filter … classify … review … write              # 13 privacy-filter sweep (pf README)
python $R/pipeline/bundle/build_bundle.py && python $R/pipeline/bundle/validate_bundle.py   # 14 rebuild with the new lists
python $R/docs/questions/make_questions.py --data <bundle copy> --cache <dir> --out $R/web/public/questions/questions.json --md $R/docs/QUESTIONS.md   # 15 questions
cd $R/web && bun install && bun run build && rsync -a --delete dist/ $W/web-dist/           # 16 web
python $R/pipeline/serve/serve.py && curl -s http://127.0.0.1:8790/api/health               # 17 serve and check
```

Before anything goes public: scan every committed and published byte against the name and id lists, and look for dataset rows and secrets.
