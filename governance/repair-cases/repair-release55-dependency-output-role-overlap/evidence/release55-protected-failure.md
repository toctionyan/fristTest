# Protected Release #55 failure

- Exact source branch: main
- Release workflow run: 31923348988
- Source SHA: b1ab036566f17d7ea52acafd23523d1420460bae
- Signed failure artifact: production-certification-evidence-31923348988-1
- Artifact ID: 9257132257
- Artifact digest: sha256:d43774610f0ec4deeb98cdce69f28560611c3fd4ee0ac332c62239245972ff07
- Failed protected component: production-certification-bundle / real_model / semantic
- Exact error: semantic_query_then_refund_consult: goal dependency mismatch for oracle g2; expected_dependencies=['g1']; actual_dependencies=[]
- No production-closed artifact was produced.

The catalog and historical regression explicitly define `它能不能退款` as a true current-turn result reference to the preceding order-query result. This repair does not change that oracle.
