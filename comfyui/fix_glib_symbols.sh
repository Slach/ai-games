#!/bin/bash
# Script to fix glib symbol issues for decord library

# Create a wrapper library to provide the deprecated g_dir_unref function
cat > /tmp/glib_compat.c << 'EOF'
#include <glib.h>

// Provide g_dir_unref as an alias to g_dir_close for compatibility
void g_dir_unref(GDir *dir) {
    g_dir_close(dir);
}
EOF

# Compile the compatibility library
gcc -shared -fPIC -o /tmp/glib_compat.so /tmp/glib_compat.c $(pkg-config --cflags --libs glib-2.0)

# Set up the environment to preload the compatibility library
export LD_PRELOAD="/tmp/glib_compat.so:$LD_PRELOAD"

# Run the original command
exec "$@"