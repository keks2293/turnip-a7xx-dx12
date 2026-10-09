# Image build: how patches 0001–0003 get into RPM

A reference copy of the recipe that builds the mesa package for the ArmadOS
image — nothing is run here, the files sit alongside so that this repository is
self-contained:

- `BASE.env` — the Mesa version pin (26.2.3) and the tarball's SHA256;
- `build.sh` — how the `patches/0001–0003` patches are substituted into the
  RPM spec (`Patch9001–9003`) while building the image;
- the origin of each patch and external sources — `../patches/PATCHES.md`.

The patches ship in `patches/`; 0004–0011 have nothing to do with the
RPM recipe — they are applied to the source tree (`scripts/build-turnip.sh`).
