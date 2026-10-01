BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO parent (id,label,kind) VALUES (1,'a','gamma');
  EXCEPTION WHEN check_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K06';
END $$;
ROLLBACK;
