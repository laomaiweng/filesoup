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


__version__ = '0.1'
__author__ = 'Quentin Minster'


import os
import hashlib
from PyQt5.QtCore import (pyqtSignal, pyqtSlot, Qt, QDir, QObject, QRectF, QThread)
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPalette)
from PyQt5.QtWidgets import (QApplication, QFileDialog, QFormLayout, QLineEdit,
                             QMainWindow, QMessageBox, QPushButton, QWidget)


ALGORITHMS_AVAILABLE = {'md5', 'sha1', 'sha256', 'sha512'} \
                       & hashlib.algorithms_available


def read_chunk(file, chunk_size=1024):
    """Lazy function (generator) to read a file chunk by chunk.
    Default chunk size: 1k."""
    while True:
        data = file.read(chunk_size)
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

    def closeEvent(self, event):
        """Handle window close requests."""
        self.stopThread()
        event.accept()

    @pyqtSlot(str)
    def error(self, message):
        """Display error messages from the worker."""
        QMessageBox.critical(self, 'filesoup', message)

    @pyqtSlot()
    def selectFile(self, path=''):
        """Select a file and start a worker thread to compute its digests."""
        # Interrupt any currently running thread
        self.stopThread()

        # Get file to process
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
        self.edits[algorithm].setText(digest)

    @pyqtSlot(float)
    def setProgress(self, progress):
        """Update the file digest computation progress bar."""
        rect = QRectF(self.fileedit.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        stop = progress - self.gradient_span if self.gradient_span < progress else 0
        gradient.setColorAt(stop, QColor(self.gradient_color))
        gradient.setColorAt(progress, self.fileeditbase)
        stop = progress + self.gradient_span if progress < 1 - self.gradient_span else 1
        gradient.setColorAt(stop, self.fileeditbase)
        palette = self.fileedit.palette()
        palette.setBrush(QPalette.Base, QBrush(gradient))
        self.fileedit.setPalette(palette)

    def setupUi(self):
        """Setup the GUI."""
        widget = QWidget(self)
        self.setCentralWidget(widget)
        layout = QFormLayout()
        layout.setLabelAlignment(Qt.AlignRight)
        widget.setLayout(layout)

        self.filebutton = QPushButton('File', widget)
        self.filebutton.clicked.connect(self.selectFile)
        self.fileedit = QLineEdit(widget)
        self.fileedit.setReadOnly(True)
        self.fileeditbase = self.fileedit.palette().base().color()
        layout.addRow(self.filebutton, self.fileedit)
        for alg in sorted(ALGORITHMS_AVAILABLE):
            edit = QLineEdit(widget)
            edit.setReadOnly(True)
            layout.addRow('  ' + alg.upper() + '  ', edit)
            self.edits[alg] = edit

        self.setFixedWidth(864)
        self.setWindowTitle('filesoup')
        self.show()

    @pyqtSlot()
    def stopThread(self):
        """Stop the worker thread, if any."""
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
            with open(self.path, 'rb') as file:
                stat = os.stat(self.path)
                for chunk in read_chunk(file, self.chunk_size):
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
    win = FileSoupWindow()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
