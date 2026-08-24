import re


class Solution:
    def checkOnesSegment(self, s) -> bool:
        # A "segment of ones" is a maximal run of consecutive '1' characters.
        # The question asks whether there is AT MOST ONE such segment.
        #
        # The v1 generic harness runs json.loads() on the stdin blob, so "110"
        # arrives as the INT 110 while "000" is invalid JSON (leading zeros)
        # and arrives as the STRING "000". str() recovers the binary text in
        # both cases; for these inputs no leading zero is lost, because any
        # input that has one fails the JSON parse and stays a string.
        text = str(s)
        return len(re.findall(r"1+", text)) <= 1
