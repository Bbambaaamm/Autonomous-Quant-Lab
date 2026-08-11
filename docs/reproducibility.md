# Reprodukovatelnost

`verify_reproduction` načte immutable config a lineage, ověří dataset registry a vyžaduje přesný
callback strategie/verze. Bez vstupu vrací `NOT_REPRODUCIBLE` a nikdy nepoužije novější variantu.
Deterministická shoda snapshotu je `MATCH`; rozdíl je `MISMATCH` a závažný auditní signál.
