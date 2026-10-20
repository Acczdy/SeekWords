"""Literal Unicode matching and bounded excerpts, shared by readers and previews."""
import heapq

MAX_EXCERPTS = 20


def match_ranges(text, keywords):
    """Yield merged Python character ranges, including Unicode casefold expansions."""
    folded = text.casefold()
    mapping = None
    if len(folded) != len(text):
        mapping = [index for index, char in enumerate(text) for _ in char.casefold()]
    heap = []
    for key in dict.fromkeys(k.casefold() for k in keywords if k):
        index = folded.find(key)
        if index >= 0:
            heapq.heappush(heap, (index, index + len(key), key))
    current = None
    while heap:
        start, end, key = heapq.heappop(heap)
        found = folded.find(key, start + 1)
        if found >= 0:
            heapq.heappush(heap, (found, found + len(key), key))
        span = (mapping[start], mapping[end - 1] + 1) if mapping is not None else (start, end)
        if current is not None and span[0] < current[1]:
            current = current[0], max(current[1], span[1])
        else:
            if current is not None:
                yield current
            current = span
    if current is not None:
        yield current


class MatchCollector:
    def __init__(self, keywords, limit=MAX_EXCERPTS):
        self.keywords = list(keywords)
        self.limit = limit
        self.matched = set()
        self.excerpts = []
        self.count = 0
        self.previous = None
        self.tail = ""
        self.line = self.column = 1
        self.offset = 0
        self.last_end = -1
        self.overlap = max((len(k.casefold()) for k in keywords), default=1)

    @staticmethod
    def position(text, index, line, column):
        prefix = text[:index]
        breaks = prefix.count("\n")
        return line + breaks, index - prefix.rfind("\n") if breaks else column + index

    def feed(self, location, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        if location != self.previous:
            self.tail, self.line, self.column = "", 1, 1
            self.offset, self.last_end = 0, -1
        self.previous = location
        for offset in range(0, len(text), 65536):
            part = text[offset:offset + 65536]
            combined = self.tail + part
            old = len(self.tail)
            folded = combined.casefold()
            self.matched.update(k for k in self.keywords if k.casefold() in folded)
            for start, end in match_ranges(combined, self.keywords):
                if end <= old:
                    continue
                absolute_start = self.offset - old + start
                absolute_end = self.offset - old + end
                overlaps_previous = absolute_start < self.last_end
                if absolute_end <= self.last_end:
                    continue
                self.last_end = absolute_end
                if not overlaps_previous:
                    self.count += 1
                if self.count > self.limit:
                    continue
                line, column = self.position(combined, start, self.line, self.column)
                left = max(0, start - 70)
                right = min(len(combined), end + 130)
                entry = {"location": location, "line": line, "column": column,
                         "position": f"{location} · 第 {line} 行，第 {column} 列",
                         "text": combined[left:right],
                         "highlight_start": start - left, "highlight_end": end - left,
                         "keywords": [k for k in self.keywords if k.casefold() in combined[start:end].casefold()]}
                if overlaps_previous and self.excerpts:
                    self.excerpts[-1] = entry
                else:
                    self.excerpts.append(entry)
            keep = min(self.overlap, len(combined))
            cut = len(combined) - keep
            self.line, self.column = self.position(combined, cut, self.line, self.column)
            self.tail = combined[cut:]
            self.offset += len(part)

    def result(self):
        first = self.excerpts[0] if self.excerpts else {}
        return {"matched": [k for k in self.keywords if k in self.matched],
                "location": first.get("position", ""), "snippet": first.get("text", ""),
                "matches": self.excerpts, "match_count": self.count,
                "matches_limited": self.count > len(self.excerpts)}
