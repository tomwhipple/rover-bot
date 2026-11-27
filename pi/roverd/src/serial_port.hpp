// A raw 115200 8N1 serial device (a tty or a pty slave), non-blocking.
#pragma once

#include <cstddef>
#include <string>
#include <utility>

#include <sys/types.h>

namespace roverd {

class SerialPort {
public:
    explicit SerialPort(std::string path) : path_(std::move(path)) {}
    ~SerialPort() { close(); }
    SerialPort(const SerialPort&) = delete;
    SerialPort& operator=(const SerialPort&) = delete;

    // On failure returns false and leaves the reason in error().
    bool open();
    void close();
    bool is_open() const { return fd_ >= 0; }
    int fd() const { return fd_; }
    const std::string& path() const { return path_; }
    const std::string& error() const { return error_; }

    // > 0 bytes read; 0 nothing available; -1 the device is gone (closed by this call).
    ssize_t read(char* buf, size_t cap);
    // Writes all of `data`, waiting briefly for the device to drain. False when it could not.
    bool write(const std::string& data);

private:
    std::string path_;
    std::string error_;
    int fd_ = -1;
};

}  // namespace roverd
