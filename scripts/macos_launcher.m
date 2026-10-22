#import <Foundation/Foundation.h>
#include <errno.h>
#include <spawn.h>
#include <sys/wait.h>

extern char **environ;

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *root = [[[NSBundle mainBundle] bundlePath] stringByDeletingLastPathComponent];
        // Keep this signed native process alive as the responsible application
        // for macOS Documents access. Do not exec Bash in its place.
        char **args = calloc((size_t)argc + 3, sizeof(char *));
        if (!args) return 1;
        args[0] = "/bin/bash";
        args[1] = (char *)[[root stringByAppendingPathComponent:@"Lancer-Jardin.command"] fileSystemRepresentation];
        args[2] = "--no-console";
        // Preserve UTF-8 arguments byte for byte (NSTask normalizes Unicode).
        for (int i = 1; i < argc; ++i) args[i + 2] = (char *)argv[i];
        pid_t child;
        int error = posix_spawn(&child, args[0], NULL, NULL, args, environ);
        free(args);
        if (error) { NSLog(@"Launch failed: %s", strerror(error)); return 1; }
        int status;
        while (waitpid(child, &status, 0) == -1) {
            if (errno != EINTR) return 1;
        }
        return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
    }
}
