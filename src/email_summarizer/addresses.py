"""Sender address rules shared by the settings loader and the Gmail query builder.

Kept neutral (no config or gmail imports) so both can depend on it without a cycle.
"""

import re

# RFC 5321 limit on a forward path; also keeps any single sender well inside one Gmail query.
MAX_ADDRESS_LENGTH = 254

# Only characters with no meaning in Gmail search syntax. No whitespace (would end the
# from: term), no ( ) { } " (grouping, quoting) and no leading "-" (negation), so a sender
# can never change what a query means.
# re.ASCII matters: with Unicode case-folding, IGNORECASE would also accept look-alikes such
# as dotless "ı" (U+0131) or the Kelvin sign (U+212A).
SAFE_ADDRESS = re.compile(
    r"[a-z0-9][a-z0-9._+-]*@[a-z0-9-]+(?:\.[a-z0-9-]+)+", re.IGNORECASE | re.ASCII
)
