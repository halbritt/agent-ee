# Executed removal measurement reader

executed-power-analysis.py is the exact agent-ee Git blob at
2c42f380:src/agent_ee/acquisition_power_analysis.py, SHA-256
0d9cc96a15de733da82d2bd5dbf67ee556c0c5ecc33a23655d0122d4b56c56b9.
The removal declaration and all eight retained case input-pins bind these bytes.
Do not reformat the artifact or replace its hash with the current reader's hash.

Historical film-removal replay imports this file as a standalone module and calls
film_measurements. Its measurement/parser path uses only hashlib, math and re.
The old package-relative imports inside inputs() belong to the historical
producer and are unused by this replay. Current production power_analysis.py
continues to own new application inputs.

The attach and replay whole-file SHA checks, case execution/trace checks and
electrical limits remain required. Replaying retained outputs grants no fresh
simulation, physical validation, production acceptance or file release.
