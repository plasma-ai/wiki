# Examples

Runnable, self-contained walkthroughs and committed sample trees. Scripts build
everything in a scratch directory and never touch the repository they live in;
committed trees are data a reader inspects.

- [`hello/`](hello/) -- a minimal seeded wiki, committed as data to inspect
  rather than a script to run: the `wiki init` scaffold, one bare page, and one
  `wiki update` to link it into the index.

```bash
cd examples/hello
wiki map
```
