# Changelog

Tested on RPCS3 0.0.42-20001.

## 1.3.1
- New: US version (BLUS30443 v01.00, PPU-83681f6110d33442329073b72b8dc88a2f677172). Same code as EU 1.3; not yet tested in game.
- Patch files renamed per region: `DeS_OmniRoll_EU_BLES00932.yml`, `DeS_OmniRoll_US_BLUS30443.yml`. The EU patch content is unchanged.
- Builder: `--region EU|US`, plus new `--elf` checks (cave in unreachable code, telemetry block unreferenced).
- Status tool detects the region.

## 1.3 (first public release, EU BLES00932 v01.00)
- Back and back-diagonal rolls use the game's back-roll animation aimed along the stick, so the character never faces more than 90 degrees away from the target. This fixes back rolls being cancelled on the first press.

## 1.2 (internal)
- Fixed mirrored left/right.

## 1.1 (internal)
- Roll direction is measured from the direction to the lock-on target, so chained rolls no longer drift ("square" pattern).
- The roll's facing is held for 0.3 s so lock-on can't pull it back toward the enemy at roll start.
- Fixed a register reload after a function call.

## 1.0 (internal)
- First working version: locked-on rolls redirected to a forward roll in the stick direction.
