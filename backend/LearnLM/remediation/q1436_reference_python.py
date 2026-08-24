class Solution:
    def destCity(self, paths: list[list[str]]) -> str:
        """
        The destination city: the one that is never a source.

        The statement defines the answer structurally, not by traversal --
        "the city that is the destination of all paths" with the guarantee
        that exactly one such city exists. Every city that has an outgoing
        edge is, by definition, not the last stop; the one city that appears
        only as a destination is.

        This is deliberately NOT a walk from a starting city. A traversal
        implementation has to decide where to start, and the edge list is not
        ordered: Example 2 gives [["B","D"],["A","B"],["C","D"]], whose first
        edge starts at B, which is not the start of anything. It also assumes
        a single chain, which Example 2 and case 4 both violate -- they are
        trees converging on one sink, not paths. Reading the edges as a chain
        to be followed is exactly the defective interpretation this question
        was repaired away from, and it produces "D" here only by accident of
        ordering, not by construction.

        Set difference answers it in one pass, independent of edge order,
        branch count and chain length.
        """
        sources = {source for source, _destination in paths}
        for _source, destination in paths:
            if destination not in sources:
                return destination
        return ""
