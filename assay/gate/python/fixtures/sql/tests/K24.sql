BEGIN;
DO $$
BEGIN
  BEGIN
    INSERT INTO parent (id,label,priority) VALUES (1,'a',4);
  EXCEPTION WHEN check_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K24';
END $$;
ROLLBACK;
