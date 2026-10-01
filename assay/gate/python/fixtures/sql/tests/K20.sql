BEGIN;
DO $$
BEGIN
  INSERT INTO child (id,parent_id,owner_id,slot,qty) VALUES (1,NULL,NULL,NULL,1);
  INSERT INTO shipment VALUES (1,1);
  BEGIN
    DELETE FROM child WHERE id=1;
  EXCEPTION WHEN foreign_key_violation OR restrict_violation THEN RETURN;
  END;
  RAISE EXCEPTION 'K20';
END $$;
ROLLBACK;
