package app

func H() int {
	f := func() int { return 7 }
	_ = f
	return 1
}
