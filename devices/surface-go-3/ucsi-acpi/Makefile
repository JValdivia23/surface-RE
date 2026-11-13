KVER ?= $(shell uname -r)
KDIR ?= /lib/modules/$(KVER)/build
PWD := $(shell pwd)

IS_CLANG := $(shell grep -q "^CONFIG_CC_IS_CLANG=y" $(KDIR)/.config 2>/dev/null && echo 1)
LLVM_FLAG := $(if $(IS_CLANG),LLVM=1,)

all:
	$(MAKE) -C $(KDIR) M=$(PWD)/src $(LLVM_FLAG) modules

clean:
	$(MAKE) -C $(KDIR) M=$(PWD)/src $(LLVM_FLAG) clean
