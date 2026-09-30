BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO parent (id,label,priority) VALUES (1,'a',9);
  EXCEPTION WHEN check_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K05';
END $$;
ROLLBACK;
