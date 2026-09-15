# Feedback

Three questions. Prose is fine — a paragraph each beats a filled-in form.

Run the test kit first — one command, and it writes the report for you:

```bash
./testkit/run.sh --quick        # macOS, Linux, WSL2
.\testkit\run.ps1 --quick       # Windows
```

Attach the `testkit-report.md` it produces. It carries no note content, no vault paths and no
username, and a failed step in it tells us more than a successful one. See [testkit/](testkit/).

---

**1. Getting it running.** Where did you stop, restart, or have to look something up? If you hit the
corporate proxy or a blocked download, which step and which tool?

**2. After a week.** What did you actually use it for? What did you expect to be there and was not?
Is there anything you now do here that you used to do somewhere else?

**3. The local model.** Did you keep using it? What did it get right, and what did it get wrong in a
way that made you stop trusting it?

---

**Anything that broke.** Command, what you expected, what happened. Paste the traceback if there was
one.

**The honest question:** if we took this away next week, would you notice?
