BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'p');
  BEGIN
    UPDATE parent SET label='q' WHERE id=1;
  EXCEPTION WHEN raise_exception THEN RETURN;
  END;
  RAISE EXCEPTION 'K21';
END $$;
ROLLBACK;
