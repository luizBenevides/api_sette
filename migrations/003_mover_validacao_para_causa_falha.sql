BEGIN;

UPDATE logs_producao
   SET causa_falha = SPLIT_PART(motivo_tratamento, ':', 1),
       api_response_raw = NULL
 WHERE tratamento_clp = 'M2100';

COMMIT;
