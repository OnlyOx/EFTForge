"""Keep selection integral and recover physical slots with bipartite matching."""

from collections import defaultdict, deque

import numpy as np
from scipy.optimize import linear_sum_assignment


class PlacementModel:
    def __init__(self, weapon, mods, compat_map, item_ids, idx):
        self.weapon = weapon
        self.mods = {i: mods[i] for i in item_ids}
        self.idx = idx
        self.slots = {
            s: (
                compat_map.slot_owner[s],
                compat_map.slots_by_id[s].required,
                tuple(sorted({i for i in allowed if i in idx})),
            )
            for s, allowed in compat_map.slot_items.items()
            if compat_map.slot_owner[s] == weapon.id or compat_map.slot_owner[s] in idx
        }
        blocked = defaultdict(set)
        for i, item in self.mods.items():
            for s in filter(None, (item.conflicting_slot_ids or "").split(",")):
                blocked[s].add(i)
        for s in filter(None, (weapon.conflicting_slot_ids or "").split(",")):
            blocked[s].add(weapon.id)
        self.blockers = {s: tuple(sorted(items)) for s, items in blocked.items()}
        self._check_dag()
        groups = {}
        for s, (owner, required, allowed) in self.slots.items():
            blockers = self.blockers.get(s, ())
            # Merge identical destinations across owners and required/optional slots.
            # Keep activation and mandatory occupancy as separate capacities below.
            key = allowed, blockers
            groups.setdefault(key, []).append(s)
        self.groups = list(groups.items())
        self.slot_group = {s: g for g, (_, slots) in enumerate(self.groups) for s in slots}
        incoming = defaultdict(list)
        for g, ((allowed, blockers), slots) in enumerate(self.groups):
            for i in allowed:
                incoming[i].append(g)
        self.incoming = incoming
        self.expressions = {}
        self.variable_count = 0

    def _check_dag(self):
        graph = defaultdict(set)
        degree = {self.weapon.id: 0, **{i: 0 for i in self.mods}}
        for owner, required, allowed in self.slots.values():
            for i in allowed:
                if i not in graph[owner]:
                    graph[owner].add(i)
                    degree[i] += 1
        pending = deque(i for i, count in degree.items() if not count)
        count = 0
        while pending:
            owner = pending.popleft()
            count += 1
            for i in graph[owner]:
                degree[i] -= 1
                if not degree[i]:
                    pending.append(i)
        if count != len(degree):
            raise ValueError("Cyclic attachment graph requires connectivity constraints.")

    def add_constraints(self, cb):
        start = cb.n
        column = start
        for i in self.idx:
            destinations = self.incoming[i]
            if not destinations:
                cb.eq({self.idx[i]: 1}, 0)
            elif len(destinations) == 1:
                self.expressions[i, destinations[0]] = self.idx[i]
            else:
                link = {self.idx[i]: -1}
                for g in destinations:
                    self.expressions[i, g] = column
                    link[column] = 1
                    column += 1
                cb.eq(link, 0)
        cb.n = column
        self.variable_count = column - start
        for g, ((allowed, blockers), slots) in enumerate(self.groups):
            occupied = {self.expressions[i, g]: 1 for i in allowed}
            row = dict(occupied)
            required_row = dict(occupied)
            capacity = required_capacity = required_count = 0
            for s in slots:
                owner, required, _allowed = self.slots[s]
                if owner == self.weapon.id:
                    capacity += 1
                    required_capacity += int(required)
                else:
                    column = self.idx[owner]
                    row[column] = row.get(column, 0) - 1
                    if required:
                        required_row[column] = required_row.get(column, 0) - 1
                required_count += int(required)
            if required_count == len(slots):
                cb.eq(row, capacity)
            else:
                cb.le(row, capacity)
                if required_count:
                    cb.ge(required_row, required_capacity)
            for blocker in blockers:
                row = dict(occupied)
                exempt = self.expressions.get((blocker, g))
                if exempt is not None:
                    row.pop(exempt, None)
                if blocker == self.weapon.id:
                    cb.le(row, 0)
                else:
                    bound = min(len(slots), len(allowed) - int(exempt is not None))
                    row[self.idx[blocker]] = row.get(self.idx[blocker], 0) + bound
                    cb.le(row, bound)
        conflicts = set()
        for i, item in self.mods.items():
            for j in (item.conflicting_item_ids or "").split(","):
                if j in self.idx:
                    conflicts.add(tuple(sorted((i, j))))
        for i, j in conflicts:
            cb.le({self.idx[i]: 1, self.idx[j]: 1}, 1)
        for i in (self.weapon.conflicting_item_ids or "").split(","):
            if i in self.idx:
                cb.eq({self.idx[i]: 1}, 0)
        cb.placement = self

    def match(self, selected):
        selected = set(selected)
        if not selected <= self.mods.keys():
            return None
        for i in selected:
            if set((self.mods[i].conflicting_item_ids or "").split(",")) & selected:
                return None
        if set((self.weapon.conflicting_item_ids or "").split(",")) & selected:
            return None
        active = [
            (s, owner, required, allowed)
            for s, (owner, required, allowed) in self.slots.items()
            if owner == self.weapon.id or owner in selected
        ]
        ordered = sorted(selected)
        m, n = len(active), len(ordered)
        if n > m:
            return None
        if not m:
            return [] if not n else None
        cost = np.full((m, m), 1e6)
        active_blockers = [
            set(self.blockers.get(s, ())) & (selected | {self.weapon.id}) for s, owner, required, allowed in active
        ]
        for r, i in enumerate(ordered):
            for c, (s, owner, required, allowed) in enumerate(active):
                if i in allowed and not (active_blockers[c] - {i}):
                    cost[r, c] = 0
        for c, (s, owner, required, allowed) in enumerate(active):
            if not required:
                cost[n:, c] = 0
        rows, cols = linear_sum_assignment(cost)
        if np.any(cost[rows, cols] >= 1e6):
            return None
        chosen = {ordered[r]: (active[c][0], active[c][1]) for r, c in zip(rows, cols) if r < n}
        children = defaultdict(list)
        for i, (s, owner) in chosen.items():
            children[owner].append(i)
        pairs, visited = [], set()
        pending = deque(children[self.weapon.id])
        while pending:
            i = pending.popleft()
            if i in visited:
                return None
            visited.add(i)
            pairs.append([chosen[i][0], i])
            pending.extend(children[i])
        return pairs if len(visited) == n else None

    def fill_assignment(self, assignment, pairs):
        for s, i in pairs:
            assignment[self.expressions[i, self.slot_group[s]]] = 1
