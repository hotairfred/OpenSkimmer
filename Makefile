CC	:= g++
INCDIRS	:=
LIBDIRS	:=
LIBS	:= -lcsdr++ -lfftw3f
CFLAGS	:= -O3 $(INCDIRS)
OBJECTS	:= cw-skimmer.o rtty-skimmer.o bufmodule.o
BREW_PREFIX ?= $(shell brew --prefix 2>/dev/null || printf /opt/homebrew)

all: csdr-cwskimmer csdr-rttyskimmer libcw_dispatcher.so libpfb_scanner.so

# Minimal native set for SparkGap's HPSDR + ITILA path on Apple Silicon.
# The .so suffix is retained because the Python loader uses these names;
# the files themselves are Mach-O dynamic libraries.
macos-native:
	/usr/bin/clang -O2 -dynamiclib -fPIC -pthread -o libhpsdr_fast.so hpsdr_fast.c -lm
	/usr/bin/clang -O3 -ffast-math -dynamiclib -fPIC -o libitila.so itila_core.c fb_core.c -lm
	/usr/bin/clang -O3 -ffast-math -dynamiclib -fPIC -o libitila_dsp.so itila_dsp.c -lm
	/usr/bin/clang -O3 -ffast-math -dynamiclib -fPIC -pthread -o libitila_scanner.so itila_scanner.c -lm
	/usr/bin/clang++ -x c++ -O3 -ffast-math -dynamiclib -fPIC -pthread \
	    -I$(BREW_PREFIX)/include -L$(BREW_PREFIX)/lib \
	    -o libpfb_scanner.so pfb_scanner.c cw_pfb.cpp -lfftw3f -lm

csdr-cwskimmer: cw-skimmer.o bufmodule.o
	$(CC) $(CFLAGS) -o $@ $^ $(LIBDIRS) $(LIBS)

csdr-rttyskimmer: rtty-skimmer.o bufmodule.o
	$(CC) $(CFLAGS) -o $@ $^ $(LIBDIRS) $(LIBS)

# Parallel CW decoder dispatcher (uhsdr + bmorse fan-out via OpenMP, PFB inside).
libcw_dispatcher.so: cw_dispatcher.cpp cw_dispatcher.h cw_pfb.cpp cw_pfb.h uhsdr_cw_lib.cpp uhsdr_cw_lib.h uhsdr_shim.h libbmorse.h libbmorse.so
	$(CC) -O3 -fPIC -fopenmp -shared -o $@ cw_dispatcher.cpp cw_pfb.cpp uhsdr_cw_lib.cpp -I. -L. -Wl,-rpath,'$$ORIGIN' -lbmorse -lfftw3f -lm

# PFB-backed band scanner — drop-in replacement for libitila_scanner.so when
# use_pfb_scanner: true is set.  Replaces per-bin NCO+FIR with a shared PFB.
libpfb_scanner.so: pfb_scanner.c pfb_scanner.h cw_pfb.cpp cw_pfb.h
	$(CC) -O3 -march=native -ffast-math -shared -fPIC \
	    -o $@ pfb_scanner.c cw_pfb.cpp -lfftw3f -lm

cw_dispatcher_test: cw_dispatcher_test.cpp cw_dispatcher.h libcw_dispatcher.so
	$(CC) -O2 -Wall -o $@ cw_dispatcher_test.cpp -L. -lcw_dispatcher -fopenmp

cw_dispatcher_pfb_test: cw_dispatcher_pfb_test.cpp cw_dispatcher.h libcw_dispatcher.so
	$(CC) -O2 -Wall -o $@ cw_dispatcher_pfb_test.cpp -L. -lcw_dispatcher -fopenmp

dispatcher-test: cw_dispatcher_test cw_dispatcher_pfb_test
	LD_LIBRARY_PATH=. ./cw_dispatcher_test
	@echo
	LD_LIBRARY_PATH=. ./cw_dispatcher_pfb_test

clean:
	rm -f $(OBJECTS) csdr-cwskimmer csdr-rttyskimmer \
	      libcw_dispatcher.so libpfb_scanner.so \
	      cw_dispatcher_test cw_dispatcher_pfb_test

.PHONY: all clean dispatcher-test macos-native
