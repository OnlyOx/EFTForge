"""Project slot placement onto selection variables and refine with Hall inequalities."""

from collections import defaultdict, deque
import copy

from scipy.sparse import csr_array
from scipy.sparse.csgraph import maximum_bipartite_matching

from optimizer.placement import PlacementModel

# Static rows depend only on the graph signature, so every solve in a request can
# replay them instead of regenerating hundreds of Hall rows. Keep only a few graphs.
_STATIC_ROWS = {}
_STATIC_ROWS_LIMIT = 8


class MatchingPlacementModel(PlacementModel):
    mode = "matching_cuts"

    def __init__(self, weapon, mods, compat_map, item_ids, idx, cut_cache=None):
        super().__init__(weapon, mods, compat_map, item_ids, idx)
        # Bind learned rows to this graph and column order, even for direct callers.
        signature = (
            weapon.id,
            tuple(item_ids),
            tuple(sorted(self.slots.items())),
            tuple(sorted(self.blockers.items())),
            tuple((i, self.mods[i].conflicting_item_ids or "") for i in item_ids),
            weapon.conflicting_item_ids or "",
        )
        self.signature = signature
        self.shared_cuts = cut_cache.setdefault(signature, {}) if cut_cache is not None else None
        self.shared_cut_count = 0
        self._blocker_sets = {slot: frozenset(items) for slot, items in self.blockers.items() if items}

    def for_solve(self, cut_cache=None):
        # Share the read-only graph, but bind each solve to its own learned cuts.
        placement = copy.copy(self)
        placement.expressions = {}
        placement.shared_cuts = cut_cache.setdefault(self.signature, {}) if cut_cache is not None else None
        placement.shared_cut_count = 0
        return placement

    def _eligible(self, slot, item, selected_blockers):
        # Most slots have no blockers at all, so skip the set math for them.
        blockers = self._blocker_sets.get(slot)
        if not blockers:
            return True
        return not (blockers & (selected_blockers | {self.weapon.id}) - {item})

    def _add_row(self, cb, row, rhs, remember=False):
        row = {c: v for c, v in row.items() if v}
        if not hasattr(cb, "matching_cut_keys"):
            cb.matching_cut_keys = set()
        key = tuple(sorted(row.items())), rhs
        if key in cb.matching_cut_keys:
            return False
        cb.matching_cut_keys.add(key)
        cb.le(row, rhs)
        if remember and self.shared_cuts is not None:
            self.shared_cuts[key] = dict(row), rhs
        return True

    def _upper_cut(self, cb, items, selected_blockers=(), remember=False):
        items, selected_blockers = set(items), set(selected_blockers)
        row = {self.idx[i]: 1 for i in items}
        capacity = 0
        neighbors = {slot for i in items for slot in self.item_slots[i] if self._eligible(slot, i, selected_blockers)}
        for slot in sorted(neighbors):
            owner, required, allowed = self.slots[slot]
            if owner == self.weapon.id:
                capacity += 1
            else:
                col = self.idx[owner]
                row[col] = row.get(col, 0) - 1
        margin = len(items)
        for blocker in selected_blockers:
            row[self.idx[blocker]] = row.get(self.idx[blocker], 0) + margin
        return self._add_row(cb, row, capacity + margin * len(selected_blockers), remember)

    def _lift(self, items, selected_blockers):
        # Widen a Hall set to every item that only fits inside the same slots. The
        # neighborhood and capacity stay the same, so the row stays valid, but one
        # cut now covers each muzzle device that could pair with the same adapter
        # instead of rejecting those pairs one native solve at a time.
        items, selected_blockers = set(items), set(selected_blockers)
        neighbors = {slot for i in items for slot in self.item_slots[i] if self._eligible(slot, i, selected_blockers)}
        for i, slots in self.item_slots.items():
            if i in items or i in selected_blockers:
                continue
            eligible = {slot for slot in slots if self._eligible(slot, i, selected_blockers)}
            if eligible and eligible <= neighbors:
                items.add(i)
        return items

    def _lower_cut(self, cb, slots, selected_blockers=(), remember=False):
        selected_blockers = set(selected_blockers)
        row, neighbors, capacity = {}, set(), 0
        for slot in slots:
            owner, required, allowed = self.slots[slot]
            assert required
            if owner == self.weapon.id:
                capacity += 1
            else:
                col = self.idx[owner]
                row[col] = row.get(col, 0) + 1
            neighbors.update(i for i in allowed if self._eligible(slot, i, selected_blockers))
        for i in neighbors:
            row[self.idx[i]] = row.get(self.idx[i], 0) - 1
        margin = len(slots)
        for blocker in selected_blockers:
            row[self.idx[blocker]] = row.get(self.idx[blocker], 0) + margin
        return self._add_row(cb, row, margin * len(selected_blockers) - capacity, remember)

    def add_constraints(self, cb):
        cached = _STATIC_ROWS.get(self.signature)
        if cached is None:
            start = len(cb.rows)
            self._add_static_constraints(cb)
            cached = self.item_slots, cb.rows[start:], frozenset(getattr(cb, "matching_cut_keys", ()))
            if len(_STATIC_ROWS) >= _STATIC_ROWS_LIMIT:
                _STATIC_ROWS.clear()
            _STATIC_ROWS[self.signature] = cached
        else:
            self.item_slots, rows, keys = cached
            cb.rows.extend(rows)
            if not hasattr(cb, "matching_cut_keys"):
                cb.matching_cut_keys = set()
            cb.matching_cut_keys.update(keys)
        if self.shared_cuts is not None:
            # Snapshot first: parallel Explore solves may learn new cuts meanwhile.
            for row, rhs in list(self.shared_cuts.values()):
                self.shared_cut_count += self._add_row(cb, row, rhs)
        cb.placement = self

    def _add_static_constraints(self, cb):
        incoming = {i: [] for i in self.idx}
        for slot, (owner, required, allowed) in self.slots.items():
            for i in allowed:
                incoming[i].append(slot)
        self.item_slots = incoming
        for i, slots in incoming.items():
            owners = {self.slots[s][0] for s in slots}
            if self.weapon.id not in owners:
                row = {self.idx[i]: 1}
                for owner in owners:
                    row[self.idx[owner]] = row.get(self.idx[owner], 0) - 1
                cb.le(row, 0)
        for (allowed, blockers), slots in self.groups:
            if allowed:
                self._upper_cut(cb, allowed)
            required = [s for s in slots if self.slots[s][1]]
            if required:
                self._lower_cut(cb, required)
        # Start with Hall bounds for items that share the same physical destinations.
        item_groups = defaultdict(list)
        required_by_owner = defaultdict(list)
        for i, slots in incoming.items():
            item_groups[tuple(sorted(slots))].append(i)
        for items in item_groups.values():
            self._upper_cut(cb, items)
        for slot, (owner, required, allowed) in self.slots.items():
            if required:
                required_by_owner[owner].append(slot)
        for slots in required_by_owner.values():
            self._lower_cut(cb, slots)
        required = [s for s, (owner, req, allowed) in self.slots.items() if req]
        if required:
            self._lower_cut(cb, required)
        conflicts = set()
        for i, item in self.mods.items():
            for j in (item.conflicting_item_ids or "").split(","):
                if j in self.idx:
                    conflicts.add(tuple(sorted((i, j))))
            blocked = set(filter(None, (item.conflicting_slot_ids or "").split(",")))
            if blocked:
                for j, slots in incoming.items():
                    if j != i and slots and set(slots) <= blocked:
                        conflicts.add(tuple(sorted((i, j))))
        for i, j in conflicts:
            cb.le({self.idx[i]: 1, self.idx[j]: 1}, 0 if i == j else 1)
        for i in (self.weapon.conflicting_item_ids or "").split(","):
            if i in self.idx:
                cb.eq({self.idx[i]: 1}, 0)

    def add_matching_cut(self, cb, selected):
        selected = sorted(selected)
        selected_set = set(selected)
        blockers = {i for i in selected if self.mods[i].conflicting_slot_ids}
        active = [
            s for s, (owner, req, allowed) in self.slots.items() if owner == self.weapon.id or owner in selected_set
        ]
        adjacency = [
            {c for c, s in enumerate(active) if i in self.slots[s][2] and self._eligible(s, i, blockers)}
            for i in selected
        ]
        n, m = len(selected), len(active)
        optional = {c for c, s in enumerate(active) if not self.slots[s][1]}
        adjacency += [optional] * max(0, m - n)
        rr, cc = [], []
        for r, neighbors in enumerate(adjacency):
            for c in neighbors:
                rr.append(r)
                cc.append(c)
        graph = csr_array(([1] * len(rr), (rr, cc)), shape=(len(adjacency), m))
        matching = maximum_bipartite_matching(graph, perm_type="column")
        reverse = {c: r for r, c in enumerate(matching) if c >= 0}
        left = {r for r, c in enumerate(matching) if c < 0}
        pending, right = deque(left), set()
        while pending:
            for c in adjacency[pending.popleft()]:
                if c in right:
                    continue
                right.add(c)
                r = reverse.get(c)
                if r is not None and r not in left:
                    left.add(r)
                    pending.append(r)
        if any(r >= n for r in left):
            required = [s for c, s in enumerate(active) if c not in right and self.slots[s][1]]
            if required and self._lower_cut(cb, required, blockers, remember=True):
                return True
        elif left and self._upper_cut(cb, self._lift([selected[r] for r in left], blockers), blockers, remember=True):
            return True
        # Keep progress even for an unusual conflict that has no matching witness.
        row = {self.idx[i]: 1 if i in selected_set else -1 for i in self.idx}
        return self._add_row(cb, row, len(selected) - 1, remember=True)

    def fill_assignment(self, assignment, pairs):
        # Matching does not add position columns to the selection-only model.
        pass
