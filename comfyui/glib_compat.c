#include <glib.h>
void g_dir_unref(GDir *dir) { g_dir_close(dir); }