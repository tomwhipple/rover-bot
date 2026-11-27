#include "serial_port.hpp"

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>

namespace roverd {

bool SerialPort::open() {
    close();
    const int fd = ::open(path_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        error_ = path_ + ": " + std::strerror(errno);
        return false;
    }
    termios tio{};
    if (::tcgetattr(fd, &tio) != 0) {
        error_ = path_ + ": tcgetattr: " + std::strerror(errno);
        ::close(fd);
        return false;
    }
    ::cfmakeraw(&tio);
    tio.c_cflag &= ~static_cast<tcflag_t>(CSIZE | PARENB | CSTOPB | CRTSCTS);
    tio.c_cflag |= CS8 | CLOCAL | CREAD;
    tio.c_iflag &= ~static_cast<tcflag_t>(IXON | IXOFF | IXANY);
    // VMIN 1: a drained non-blocking read is EAGAIN; with VMIN 0 some systems return 0, which
    // reads as end of file.
    tio.c_cc[VMIN] = 1;
    tio.c_cc[VTIME] = 0;
    ::cfsetispeed(&tio, B115200);
    ::cfsetospeed(&tio, B115200);
    if (::tcsetattr(fd, TCSANOW, &tio) != 0) {
        error_ = path_ + ": tcsetattr: " + std::strerror(errno);
        ::close(fd);
        return false;
    }
    ::tcflush(fd, TCIOFLUSH);
    fd_ = fd;
    error_.clear();
    return true;
}

void SerialPort::close() {
    if (fd_ >= 0) ::close(fd_);
    fd_ = -1;
}

ssize_t SerialPort::read(char* buf, size_t cap) {
    if (fd_ < 0) return -1;
    for (;;) {
        const ssize_t n = ::read(fd_, buf, cap);
        if (n > 0) return n;
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return 0;
        error_ = n == 0 ? path_ + ": closed" : path_ + ": " + std::strerror(errno);
        close();
        return -1;
    }
}

bool SerialPort::write(const std::string& data) {
    size_t off = 0;
    int waits = 0;
    while (fd_ >= 0 && off < data.size()) {
        const ssize_t n = ::write(fd_, data.data() + off, data.size() - off);
        if (n > 0) {
            off += static_cast<size_t>(n);
            continue;
        }
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) && waits++ < 5) {
            pollfd p{fd_, POLLOUT, 0};
            ::poll(&p, 1, 10);
            continue;
        }
        error_ = path_ + ": write: " + std::strerror(errno);
        return false;
    }
    return fd_ >= 0;
}

}  // namespace roverd
