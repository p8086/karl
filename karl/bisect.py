from __future__ import annotations

import math


class Bisect:
    def __init__(self, candidates: list[int], deps: dict[int, list[int]]):
        self.all = list(dict.fromkeys(candidates))
        known = set(self.all)
        self.deps = {m: [d for d in deps.get(m, []) if d in known and d != m] for m in self.all}
        self.suspects = list(self.all)
        self.on: set[int] = set(self.all)
        self.off: set[int] = set()
        self.step = 0
        self.estimate = max(1, math.ceil(math.log2(max(2, len(self.all)))))
        if not self.done:
            self._plan()

    def closure(self, ids) -> set[int]:
        out, todo = set(), list(ids)
        while todo:
            m = todo.pop()
            if m in out:
                continue
            out.add(m)
            todo.extend(self.deps.get(m, []))
        return out

    def _needed_by(self, m: int, pool: set[int]) -> int:
        return sum(1 for x in pool if x != m and m in self.closure([x]))

    def _plan(self) -> None:
        pool = set(self.suspects)
        order = sorted(self.suspects, key=lambda m: (self._needed_by(m, pool), self.all.index(m)))
        half = max(1, len(order) // 2)
        for size in range(half, len(order)):
            on = self.closure(order[size:])
            off = pool - on
            if off:
                self.on, self.off = on, off
                return
        self.on, self.off = self.closure(self.suspects), set()

    @property
    def done(self) -> bool:
        return len(self.suspects) <= 1 or (self.step > 0 and not self.off)

    def states(self) -> dict[int, bool]:
        return {m: m in self.on for m in self.all}

    def answer(self, broken: bool) -> None:
        if self.done:
            return
        if broken:
            self.suspects = [m for m in self.suspects if m not in self.off]
        else:
            self.suspects = [m for m in self.suspects if m in self.off]
        self.step += 1
        if len(self.suspects) > 1:
            self._plan()
        else:
            self.off = set()

    def result(self) -> list[int]:
        return list(self.suspects)

    def required_by_result(self) -> list[int]:
        out: list[int] = []
        for m in self.suspects:
            out += [d for d in self.closure([m]) if d != m and d not in out]
        return out
