BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label) VALUES (1,'d');
  BEGIN
    INSERT INTO parent (id,label) VALUES (2,'d');
  EXCEPTION WHEN unique_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K13';
END $$;
ROLLBACK;
