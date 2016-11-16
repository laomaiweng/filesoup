#!/usr/bin/env python3

"""
filesoup file hasher

A GUI interface to Python's hashlib module.
Supported hashes are listed in hashlib.algorithms_available.

Requires:
  Python 3.2+
  PyQt5

TODOS:
* Check digests against user-provided digest
* Hash multiple files
* Export digests to file
"""


import os
import hashlib
import timeit
from PyQt5.QtCore import (pyqtSignal, pyqtSlot, Qt, QDir, QFileInfo, QObject,       # pylint: disable=no-name-in-module
                          QRectF, QThread)
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPalette)                 # pylint: disable=no-name-in-module
from PyQt5.QtWidgets import (QApplication, QFileDialog, QFormLayout, QLineEdit,     # pylint: disable=no-name-in-module
                             QMainWindow, QMessageBox, QPushButton, QWidget)


__version__ = '0.1'
__author__ = 'Quentin Minster'


ALGORITHMS_AVAILABLE = {'md5', 'sha1', 'sha256', 'sha512'} \
                       & hashlib.algorithms_available


def read_chunk(file_, chunk_size=1024):
    """Lazy function (generator) to read a file chunk by chunk.
    Default chunk size: 1k."""
    while True:
        data = file_.read(chunk_size)
        if not data:
            break
        yield data


class FileSoupWindow(QMainWindow):
    """The main filesoup window."""

    # Time to wait for graceful termination of a worker thread
    # If exceeded, the thread will be abruptly terminated
    thread_timeout = 1000
    # Span of the gradient at the edge of the progress bar
    # Keep this below 0.1
    gradient_span = 0.001
    # Progress bar color
    gradient_color = '#f99e41'

    def __init__(self, parent=None):
        super(FileSoupWindow, self).__init__(parent)
        self.filebutton = None
        self.fileedit = None
        self.fileeditbase = None
        self.edits = {}
        self.worker = None
        self.thread = None
        self.setupUi()
        # Process the first file provided on the command line, if any
        args = QApplication.arguments()
        if len(args) >= 2:
            self.selectFile(QFileInfo(args[1]).canonicalFilePath())

    def closeEvent(self, event):
        """Handle window close requests."""
        # pylint: disable=invalid-name
        self.stopThread()
        event.accept()

    def dragEnterEvent(self, event):
        """Handle window drag enter events."""
        #pylint: disable=invalid-name,no-self-use
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        """Handle window drop events."""
        #pylint: disable=invalid-name,no-self-use
        self.selectFile(event.mimeData().urls()[0].toLocalFile())

    @pyqtSlot(str)
    def error(self, message):
        """Display error messages from the worker."""
        QMessageBox.critical(self, 'filesoup', message)

    @pyqtSlot()
    def selectFile(self, path=None):
        """Select a file and start a worker thread to compute its digests."""
        # pylint: disable=invalid-name
        # Interrupt any currently running thread
        self.stopThread()

        # Get file to process
        if path is None:
            (path, _) = QFileDialog.getOpenFileName(self)     # getOpenFileName() returns a tuple
        if path == '':
            return
        self.fileedit.setText(QDir.toNativeSeparators(path))
        for edit in self.edits.values():
            edit.setText('')

        # Create worker and run it in a separate thread
        # A note on signals:
        # * the worker receives its signals in the new thread's event loop
        # * the thread receives its signals in the *main* thread's event loop
        thread = QThread()
        worker = FileDigestWorker(ALGORITHMS_AVAILABLE, path)
        worker.progress.connect(self.setProgress)
        worker.digested.connect(self.setDigest)
        worker.error.connect(self.error)
        worker.moveToThread(thread)
        thread.started.connect(worker.process)
        worker.finished.connect(thread.quit)
        thread.finished.connect(self.stopThread)
        thread.start(QThread.HighPriority)
        self.worker = worker
        self.thread = thread

    @pyqtSlot(str, str)
    def setDigest(self, algorithm, digest):
        """Display one of the file's digests."""
        # pylint: disable=invalid-name
        edit = self.edits[algorithm]
        edit.setText(digest)
        # Adjust the width so that the digest fits
        # (+10 for Windows where the bounding rect width isn't quite enough)
        width = edit.fontMetrics().boundingRect(digest).width()
        edit.setMinimumWidth(width + 10)

    @pyqtSlot(float)
    def setProgress(self, progress):
        """Update the file digest computation progress bar."""
        # pylint: disable=invalid-name
        rect = QRectF(self.fileedit.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        if self.gradient_span < progress:
            stop = progress - self.gradient_span
        else:
            stop = 0
        gradient.setColorAt(stop, QColor(self.gradient_color))
        gradient.setColorAt(progress, self.fileeditbase)
        if progress < 1 - self.gradient_span:
            stop = progress + self.gradient_span
        else:
            stop = 1
        gradient.setColorAt(stop, self.fileeditbase)
        palette = self.fileedit.palette()
        palette.setBrush(QPalette.Base, QBrush(gradient))
        self.fileedit.setPalette(palette)

    def setupUi(self):
        """Setup the GUI."""
        # pylint: disable=invalid-name
        # Window layout
        widget = QWidget(self)
        self.setCentralWidget(widget)
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignRight)
        widget.setLayout(layout)

        # File row
        self.filebutton = QPushButton('File', widget)
        self.filebutton.clicked.connect(self.selectFile)
        self.fileedit = QLineEdit(widget)
        self.fileedit.setReadOnly(True)
        self.fileeditbase = self.fileedit.palette().base().color()
        layout.addRow(self.filebutton, self.fileedit)

        # Digest rows
        for alg in sorted(ALGORITHMS_AVAILABLE):
            edit = QLineEdit(widget)
            edit.setReadOnly(True)
            layout.addRow('  ' + alg.upper() + '  ', edit)
            self.edits[alg] = edit
            # Let setDigest() adjust the width of each row
            digest = hashlib.new(alg)
            self.setDigest(alg, '0' * len(digest.hexdigest()))
            edit.setText('')

        self.setAcceptDrops(True)
        self.setWindowTitle('filesoup')
        self.show()

    @pyqtSlot()
    def stopThread(self):
        """Stop the worker thread, if any."""
        # pylint: disable=invalid-name
        if self.thread is not None:
            if not self.thread.isFinished():
                # Thread still running: tell the worker to stop gracefully
                self.thread.requestInterruption()
                # Explicitly stop the thread's event loop now, since it won't
                # receive the worker's finished() signal as we want to wait()
                # for it without returning to our own event loop
                self.thread.quit()
                # Grace period for the thread to properly finish
                if not self.thread.wait(self.thread_timeout):
                    self.thread.terminate()
                self.fileedit.setText('')
            # Always reset the progress bar
            self.setProgress(0)
            # Forget the worker and thread
            self.worker = None
            self.thread = None


class FileDigestWorker(QObject):
    """Worker class for computing the digests of a file."""
    # pylint: disable=too-few-public-methods

    # Chunk size (in bytes) to read between digests updates
    # Large chunks (>> digest.block_size) are more efficient
    chunk_size = 2*1024*1024            # 2MB
    # Interval (in bytes of file processed) between progress notifications
    # For smoother progression, should be a multiple of the chunk size
    progress_interval = 6*1024*1024     # 6MB

    def __init__(self, algorithms, path, parent=None):
        super(FileDigestWorker, self).__init__(parent)
        self.algorithms = algorithms
        self.path = path

    progress = pyqtSignal(float)
    digested = pyqtSignal(str, str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    @pyqtSlot()
    def process(self):
        """Compute the file's digests."""
        try:
            self.progress.emit(0)
            digests = {hashlib.new(a) for a in self.algorithms}
            size = 0
            interval_size = 0
            with open(self.path, 'rb') as file_:
                stat = os.stat(self.path)
                start_time = timeit.default_timer()
                for chunk in read_chunk(file_, self.chunk_size):
                    # Update digests
                    for digest in digests:
                        digest.update(chunk)
                    # Notify progress
                    interval_size += len(chunk)
                    if self.progress_interval <= interval_size:
                        self.progress.emit(size / stat.st_size)
                        size += interval_size
                        interval_size = 0
                    # Check for interruption request
                    if QThread.currentThread().isInterruptionRequested():
                        self.finished.emit()
                        return
                elapsed = timeit.default_timer() - start_time
                size_mb = stat.st_size / (1024 * 1024)
                print('%s: %.2f MB in %.2f s (%.2f MB/s)'
                      % (QDir.toNativeSeparators(self.path),
                         size_mb,
                         elapsed,
                         size_mb / elapsed))
            # Display digests
            self.progress.emit(1)
            for digest in digests:
                self.digested.emit(digest.name, digest.hexdigest())
            self.finished.emit()
        except Exception as ex:
            self.error.emit(str(ex))

def main():
    """Start the Qt application and GUI."""
    import sys
    app = QApplication(sys.argv)
    win = FileSoupWindow()          # pylint: disable=unused-variable
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
