package app

import "testing"

func TestAdd(t *testing.T) {
	if Add(2, 3) != 5 {
		t.Fatal("Add")
	}
}

func TestClassify(t *testing.T) {
	if Classify(11) != "big" {
		t.Fatal("Classify")
	}
}
