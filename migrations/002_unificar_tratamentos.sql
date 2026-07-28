BEGIN;

-- Mantem o registro mais recente de cada erro e remove repeticoes anteriores.
DELETE FROM logs_producao repetido
USING logs_producao original
WHERE repetido.serial = original.serial
  AND repetido.tratamento_clp = 'M2100'
  AND original.tratamento_clp = 'M2100'
  AND repetido.motivo_tratamento = original.motivo_tratamento
  AND repetido.id < original.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_logs_serial_motivo_tratamento
    ON logs_producao (serial, motivo_tratamento)
    WHERE tratamento_clp = 'M2100';

COMMIT;
