BEGIN;
DO $$
BEGIN
  INSERT INTO parent (id,label,code) VALUES (1,'a','c');
  BEGIN
    INSERT INTO parent (id,label,code) VALUES (2,'b','c');
  EXCEPTION WHEN unique_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K10';
END $$;
ROLLBACK;
