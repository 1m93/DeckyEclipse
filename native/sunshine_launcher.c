/*
 * eclipse-sunshine: starts the native Sunshine that Eclipse installs
 * ("Sunshine for Gaming Mode") with the capabilities KMS capture needs.
 *
 * Layout: <root>/bin/eclipse-sunshine (this), <root>/bin/sunshine and the
 * libraries in <root>/lib, all owned by root.
 *
 * This program carries the file capabilities cap_sys_admin,cap_sys_nice+p.
 * Sunshine itself has none: a binary with file capabilities runs in secure
 * mode, where the loader ignores LD_LIBRARY_PATH and LD_PRELOAD, and Sunshine
 * needs both (its Ubuntu libraries and libeclipse-sunshine.so). Instead the
 * capabilities are raised into the ambient set, which Sunshine inherits
 * without secure mode. Sunshine drops them from everything but its DRM worker
 * thread right after startup, so the apps it starts do not get them.
 *
 * Capabilities are only handed on when the installation and its parent
 * directory are owned by root and not writable by others; otherwise anyone
 * able to replace the Sunshine binary (or swap the installation) would get them.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static const int CAPS[] = {CAP_SYS_ADMIN, CAP_SYS_NICE};

/* Owned by root and writable only by root. */
static int trusted(const char *path) {
  struct stat st;
  return lstat(path, &st) == 0 && st.st_uid == 0 && !(st.st_mode & (S_IWGRP | S_IWOTH)) && !S_ISLNK(st.st_mode);
}

static int raise_ambient(void) {
  struct __user_cap_header_struct header = {_LINUX_CAPABILITY_VERSION_3, 0};
  struct __user_cap_data_struct data[2];
  if (syscall(SYS_capget, &header, data) != 0) {
    return -1;
  }
  int raised = 0;
  for (size_t i = 0; i < sizeof(CAPS) / sizeof(CAPS[0]); ++i) {
    unsigned idx = CAPS[i] / 32, bit = 1u << (CAPS[i] % 32);
    if (data[idx].permitted & bit) {
      data[idx].inheritable |= bit;
    }
  }
  if (syscall(SYS_capset, &header, data) != 0) {
    return -1;
  }
  for (size_t i = 0; i < sizeof(CAPS) / sizeof(CAPS[0]); ++i) {
    unsigned idx = CAPS[i] / 32, bit = 1u << (CAPS[i] % 32);
    if ((data[idx].permitted & bit) && prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_RAISE, CAPS[i], 0, 0) == 0) {
      raised |= CAPS[i] == CAP_SYS_ADMIN;
    }
  }
  return raised ? 0 : -1;
}

int main(int argc, char *argv[]) {
  (void) argc;
  char self[PATH_MAX];
  ssize_t len = readlink("/proc/self/exe", self, sizeof(self) - 1);
  if (len <= 0) {
    perror("eclipse-sunshine: readlink /proc/self/exe");
    return 127;
  }
  self[len] = '\0';

  /* self = <root>/bin/eclipse-sunshine */
  char base[PATH_MAX], root[PATH_MAX], bin_dir[PATH_MAX], lib_dir[PATH_MAX], binary[PATH_MAX], preload[PATH_MAX];
  strcpy(bin_dir, self);
  char *slash = strrchr(bin_dir, '/');
  if (!slash || slash == bin_dir) {
    fprintf(stderr, "eclipse-sunshine: unexpected location %s\n", self);
    return 127;
  }
  *slash = '\0';
  strcpy(root, bin_dir);
  slash = strrchr(root, '/');
  if (!slash || slash == root) {
    fprintf(stderr, "eclipse-sunshine: unexpected location %s\n", self);
    return 127;
  }
  *slash = '\0';
  strcpy(base, root);
  slash = strrchr(base, '/');
  if (!slash) {
    fprintf(stderr, "eclipse-sunshine: unexpected location %s\n", self);
    return 127;
  }
  if (slash == base) {
    slash[1] = '\0'; /* the parent is "/" */
  } else {
    *slash = '\0';
  }
  if (snprintf(lib_dir, sizeof(lib_dir), "%s/lib", root) >= (int) sizeof(lib_dir) ||
      snprintf(binary, sizeof(binary), "%s/sunshine", bin_dir) >= (int) sizeof(binary) ||
      snprintf(preload, sizeof(preload), "%s/libeclipse-sunshine.so", lib_dir) >= (int) sizeof(preload)) {
    fprintf(stderr, "eclipse-sunshine: path too long\n");
    return 127;
  }

  if (trusted(base) && trusted(root) && trusted(bin_dir) && trusted(lib_dir) && trusted(binary) && trusted(preload)) {
    if (raise_ambient() != 0) {
      fprintf(stderr, "eclipse-sunshine: no CAP_SYS_ADMIN, Gaming Mode capture will fail (%s). "
                      "Repair Sunshine in Eclipse's settings.\n", strerror(errno));
    }
  } else {
    fprintf(stderr, "eclipse-sunshine: %s is not owned by root, starting Sunshine without capabilities\n", root);
  }

  setenv("LD_LIBRARY_PATH", lib_dir, 1);
  setenv("LD_PRELOAD", preload, 1);
  setenv("ECLIPSE_SUNSHINE_LAUNCHER", self, 1);
  setenv("ECLIPSE_SUNSHINE_BINARY", binary, 1);
  argv[0] = binary;
  execv(binary, argv);
  fprintf(stderr, "eclipse-sunshine: cannot start %s: %s\n", binary, strerror(errno));
  return 127;
}
