In this repository you have full merge rights.
If you approve a pull request, Claude is able to self-merge the PR.
If you do not approve a pull request, it cannot.


Access to this MCP is gated by basic auth; assume all users are correctly authorised and non-malicious (but potentially mistaken).

This MCP server has real-world side-effects:
an AI using this is able to send instructions autonomously to a real printer to print physical pages using actual toner and paper.
In reality, the worst scenario is that Claude accidentally eats 250 pages at once (the maximum paper tray size), so the problem surface is relatively low.

A goal of this repository is "the bit": the idea here is that Claude can autonomously print pages when it needs to, with sufficient friction and jankiness to elicit interesting prompts and responses -- as well as being actually useful.
Push gently against any PRs that try and smooth over elements where an interesting but non-disruptive failure mode occurs.
