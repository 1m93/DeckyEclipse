/*
 * libeclipse-sunshine.so: preloaded into the native Sunshine that Eclipse
 * installs ("Sunshine for Gaming Mode"). It does two things.
 *
 * 1. Capture the primary plane (no black picture).
 *    Sunshine 2026.906 and newer name every KMS "monitor" after its connector
 *    and map a connector name to the LAST plane found on its CRTC. When
 *    gamescope scans out on several planes (Gaming Mode without forced
 *    composition), that last plane is the overlay plane, which is usually
 *    empty: the stream is black (LizardByte/Sunshine#5839). 2026.516 used the
 *    first plane, the primary one. drmModeGetPlaneResources() is wrapped so
 *    that, on a CRTC with an active primary plane, overlay planes are left out
 *    of the list. Only Sunshine sees the shorter list; gamescope and the
 *    display are not affected. Set ECLIPSE_PLANEFIX=0 to turn it off.
 *
 * 2. Restart through the launcher.
 *    The launcher (sunshine_launcher.c) hands Sunshine its capabilities and
 *    sets LD_PRELOAD / LD_LIBRARY_PATH. When Sunshine restarts itself (after a
 *    settings change in its web UI) it calls execv() on its own binary, which
 *    would start it without capabilities and without its libraries. execv()
 *    of the Sunshine binary is therefore redirected to the launcher. The
 *    launcher's variables are removed from the environment at startup, so
 *    apps and commands started by Sunshine do not inherit them.
 *
 * The dynamic loader has read LD_PRELOAD and LD_LIBRARY_PATH before this
 * library runs, so removing them does not change how Sunshine loads libraries.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* ------------------------------------------------------------------ restart */

static char launcher_path[PATH_MAX];
static char binary_path[PATH_MAX];

static void copy_env(const char *name, char *out) {
  const char *value = getenv(name);
  if (value && strlen(value) < PATH_MAX) {
    strcpy(out, value);
  }
}

__attribute__((constructor)) static void eclipse_init(void) {
  copy_env("ECLIPSE_SUNSHINE_LAUNCHER", launcher_path);
  copy_env("ECLIPSE_SUNSHINE_BINARY", binary_path);
  if (!launcher_path[0]) {
    return; /* Not started by the launcher: leave the environment alone. */
  }
  unsetenv("LD_PRELOAD");
  unsetenv("LD_LIBRARY_PATH");
  unsetenv("ECLIPSE_SUNSHINE_LAUNCHER");
  unsetenv("ECLIPSE_SUNSHINE_BINARY");
}

static int is_sunshine_binary(const char *path) {
  struct stat a, b;
  if (!path || !binary_path[0] || stat(path, &a) != 0 || stat(binary_path, &b) != 0) {
    return 0;
  }
  return a.st_dev == b.st_dev && a.st_ino == b.st_ino;
}

typedef int (*execv_fn)(const char *, char *const[]);

int execv(const char *path, char *const argv[]) {
  static execv_fn real_execv;
  if (!real_execv) {
    real_execv = (execv_fn) dlsym(RTLD_NEXT, "execv");
  }
  if (launcher_path[0] && is_sunshine_binary(path)) {
    path = launcher_path;
  }
  return real_execv(path, argv);
}

/* ------------------------------------------------------------------ plane fix */

/* Layouts of the public libdrm structs (xf86drmMode.h), first fields only. */
typedef struct {
  uint32_t count_planes;
  uint32_t *planes;
} plane_res_t;

typedef struct {
  uint32_t count_formats;
  uint32_t *formats;
  uint32_t plane_id;
  uint32_t crtc_id;
  uint32_t fb_id;
} plane_t;

typedef struct {
  uint32_t count_props;
  uint32_t *props;
  uint64_t *prop_values;
} object_props_t;

typedef struct {
  uint32_t prop_id;
  uint32_t flags;
  char name[32];
} property_t;

#define OBJECT_PLANE 0xeeeeeeeeu
#define PLANE_TYPE_OVERLAY 0
#define PLANE_TYPE_PRIMARY 1
#define PLANE_TYPE_UNKNOWN -1
#define MAX_PLANES 128

typedef plane_res_t *(*get_plane_res_fn)(int);
typedef plane_t *(*get_plane_fn)(int, uint32_t);
typedef void (*free_plane_fn)(plane_t *);
typedef object_props_t *(*get_props_fn)(int, uint32_t, uint32_t);
typedef void (*free_props_fn)(object_props_t *);
typedef property_t *(*get_prop_fn)(int, uint32_t);
typedef void (*free_prop_fn)(property_t *);

static struct {
  get_plane_res_fn get_plane_res;
  get_plane_fn get_plane;
  free_plane_fn free_plane;
  get_props_fn get_props;
  free_props_fn free_props;
  get_prop_fn get_prop;
  free_prop_fn free_prop;
} real;

static int load_real(void) {
  if (real.get_plane_res) {
    return 1;
  }
  real.get_plane = (get_plane_fn) dlsym(RTLD_NEXT, "drmModeGetPlane");
  real.free_plane = (free_plane_fn) dlsym(RTLD_NEXT, "drmModeFreePlane");
  real.get_props = (get_props_fn) dlsym(RTLD_NEXT, "drmModeObjectGetProperties");
  real.free_props = (free_props_fn) dlsym(RTLD_NEXT, "drmModeFreeObjectProperties");
  real.get_prop = (get_prop_fn) dlsym(RTLD_NEXT, "drmModeGetProperty");
  real.free_prop = (free_prop_fn) dlsym(RTLD_NEXT, "drmModeFreeProperty");
  real.get_plane_res = (get_plane_res_fn) dlsym(RTLD_NEXT, "drmModeGetPlaneResources");
  return real.get_plane_res != NULL;
}

static int plane_type(int fd, uint32_t plane_id) {
  int type = PLANE_TYPE_UNKNOWN;
  object_props_t *props = real.get_props(fd, plane_id, OBJECT_PLANE);
  if (!props) {
    return type;
  }
  for (uint32_t i = 0; i < props->count_props && type == PLANE_TYPE_UNKNOWN; ++i) {
    property_t *prop = real.get_prop(fd, props->props[i]);
    if (prop) {
      if (strcmp(prop->name, "type") == 0) {
        type = (int) props->prop_values[i];
      }
      real.free_prop(prop);
    }
  }
  real.free_props(props);
  return type;
}

static int planefix_enabled(void) {
  const char *value = getenv("ECLIPSE_PLANEFIX");
  return !value || strcmp(value, "0") != 0;
}

plane_res_t *drmModeGetPlaneResources(int fd) {
  if (!load_real()) {
    return NULL;
  }
  plane_res_t *res = real.get_plane_res(fd);
  int helpers = real.get_plane && real.free_plane && real.get_props && real.free_props && real.get_prop && real.free_prop;
  if (!res || !helpers || !planefix_enabled() || res->count_planes > MAX_PLANES) {
    return res;
  }

  uint32_t n = res->count_planes;
  int types[MAX_PLANES];
  uint32_t crtcs[MAX_PLANES];
  uint32_t fbs[MAX_PLANES];
  for (uint32_t i = 0; i < n; ++i) {
    plane_t *plane = real.get_plane(fd, res->planes[i]);
    types[i] = plane_type(fd, res->planes[i]);
    crtcs[i] = plane ? plane->crtc_id : 0;
    fbs[i] = plane ? plane->fb_id : 0;
    if (plane) {
      real.free_plane(plane);
    }
  }

  /* Keep a plane unless it is an overlay on a CRTC whose primary plane shows a
   * framebuffer. Without universal planes the list has no primary planes, so
   * nothing is removed. */
  uint32_t kept = 0;
  for (uint32_t i = 0; i < n; ++i) {
    int hide = 0;
    if (types[i] == PLANE_TYPE_OVERLAY && crtcs[i]) {
      for (uint32_t j = 0; j < n && !hide; ++j) {
        hide = types[j] == PLANE_TYPE_PRIMARY && crtcs[j] == crtcs[i] && fbs[j];
      }
    }
    if (hide) {
      fprintf(stderr, "eclipse-planefix: hiding overlay plane %u on CRTC %u\n", res->planes[i], crtcs[i]);
    } else {
      res->planes[kept++] = res->planes[i];
    }
  }
  /* The array is compacted in place, so drmModeFreePlaneResources still frees it. */
  res->count_planes = kept;
  return res;
}
