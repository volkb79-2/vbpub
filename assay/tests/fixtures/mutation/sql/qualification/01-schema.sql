-- assay SQL qualification schema (A-480). [Knn] tags name matrix rows.
-- Trap: ON DELETE RESTRICT, NOT NULL, CHECK (x), UNIQUE in a comment.
/* Trap: CREATE TRIGGER t BEFORE INSERT ON parent; REFERENCES parent (id) */
CREATE DOMAIN posint AS integer
  NOT NULL -- [K04]
  CHECK (VALUE > 0); -- [K08]
CREATE TABLE parent (
  id integer PRIMARY KEY,
  label text NOT NULL, -- [K01]
  kind text NOT NULL DEFAULT 'alpha' -- [K02]
    CHECK (kind IN ('alpha', 'beta')), -- [K06] [K25]
  priority integer DEFAULT 1
    CONSTRAINT parent_priority_domain CHECK (priority IN (1, 2, 3)), -- [K05] [K24]
  code text UNIQUE, -- [K10]
  note text DEFAULT 'IS NOT NULL in a string'
);
CREATE TABLE child (
  id integer PRIMARY KEY,
  parent_id integer REFERENCES parent (id) ON DELETE NO ACTION, -- [K15] [K19]
  owner_id integer,
  slot integer,
  qty integer,
  CONSTRAINT child_slot_unique UNIQUE (parent_id, slot), -- [K11]
  CONSTRAINT fk_child_owner FOREIGN KEY (owner_id) REFERENCES parent (id) -- [K16]
    ON DELETE RESTRICT ON UPDATE CASCADE -- [K18]
);
ALTER TABLE child ALTER COLUMN qty SET NOT NULL; -- [K03]
ALTER TABLE child ADD CONSTRAINT child_qty_positive CHECK (qty > 0) NOT VALID; -- [K07]
ALTER TABLE child ALTER COLUMN slot DROP NOT NULL;
CREATE UNIQUE INDEX parent_label_uidx ON parent (label); -- [K13]
CREATE UNIQUE INDEX child_owner_big_uidx ON child (owner_id) -- [K14]
  WHERE qty > 100 AND owner_id IS NOT NULL;
CREATE TABLE shipment (id integer PRIMARY KEY, child_id integer);
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_shipment_child') THEN
    ALTER TABLE shipment ADD CONSTRAINT fk_shipment_child
      FOREIGN KEY (child_id) REFERENCES child (id) ON DELETE RESTRICT; -- [K17] [K20]
  END IF;
END $$;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'child_qty_positive') THEN
    ALTER TABLE child ADD CONSTRAINT child_qty_positive CHECK (qty > 0); -- [K09]
  END IF;
END $$;
CREATE TABLE audit (id bigserial PRIMARY KEY, tbl text);
CREATE FUNCTION parent_freeze() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  guard integer NOT NULL := 0;
BEGIN
  IF OLD.label IS NOT NULL AND NEW.label IS DISTINCT FROM OLD.label THEN
    RAISE EXCEPTION 'label is frozen';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER parent_freeze BEFORE UPDATE ON parent -- [K21]
  FOR EACH ROW EXECUTE FUNCTION parent_freeze();
CREATE FUNCTION child_audit() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO audit (tbl) VALUES (TG_TABLE_NAME);
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER child_audit AFTER INSERT ON child -- [K22]
  DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION child_audit();
CREATE VIEW parent_labels AS SELECT DISTINCT id, label FROM parent;
CREATE FUNCTION parent_labels_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO parent (id, label) VALUES (NEW.id, NEW.label);
  RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER parent_labels_insert INSTEAD OF INSERT ON parent_labels -- [K23]
  FOR EACH ROW EXECUTE FUNCTION parent_labels_insert();
GRANT REFERENCES ON parent TO PUBLIC;
COMMENT ON TABLE parent IS 'NOT NULL CHECK (x) UNIQUE REFERENCES parent (id) ON DELETE RESTRICT';
CREATE TABLE "Trap Table" ("NOT NULL" text, "UNIQUE" text);
