# CTFd better dynamic scoring

This CTFd plugin adds the `Fixed Dynamic` challenge type.

It uses the dynamic scoring parameters (`Initial`, `Decay`, and `Minimum`) to
calculate the value for the next solve. The value awarded to a solve is saved
permanently, so later solves cannot reduce points already earned by earlier
solvers.

## Installation

Clone this repo into `CTFd/plugins/CTFd_better_dynamic_scoring` and restart
CTFd. Select `Fixed Dynamic` as the challenge type when creating a challenge.

Install it before the competition starts. Solves created before installation
do not have a historical snapshot and fall back to the current challenge
value when displayed or scored.

This plugin follows CTFd's documented challenge type plugin interface. It also
uses runtime patches for scoreboard functions because CTFd 3.8.x does not
expose a scoring hook. Those patches are version-specific.
