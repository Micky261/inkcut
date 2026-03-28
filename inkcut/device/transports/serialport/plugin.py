# -*- coding: utf-8 -*-
"""
Copyright (c) 2017-2019, Jairus Martin.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Jul 12, 2015

@author: jrm
"""
import serial
import traceback
from atom.atom import set_default
from atom.api import List, Instance, Enum, Bool, Int, Str
from inkcut.core.api import Plugin, Model, log
from inkcut.device.plugin import DeviceTransport
from twisted.internet import reactor
from twisted.internet.protocol import Protocol, connectionDone
from twisted.internet.serialport import SerialPort
from serial.tools.list_ports import comports

from inkcut.device.transports.raw.plugin import RawFdTransport, RawFdProtocol


#: Reverse key values
SERIAL_PARITIES = {v: k for k, v in serial.PARITY_NAMES.items()}


class SerialConfig(Model):
    #: Available serial ports
    ports = List()

    #: Serial port config
    port = Str().tag(config=True)
    baudrate = Int(9600).tag(config=True)
    bytesize = Enum(serial.EIGHTBITS, serial.SEVENBITS, serial.SIXBITS,
                    serial.FIVEBITS).tag(config=True)
    parity = Enum(*serial.PARITY_NAMES.values()).tag(config=True)
    stopbits = Enum(serial.STOPBITS_ONE, serial.STOPBITS_ONE_POINT_FIVE,
                    serial.STOPBITS_TWO).tag(config=True)
    xonxoff = Bool().tag(config=True)
    rtscts = Bool().tag(config=True)
    dsrdtr = Bool().tag(config=True)

    # -------------------------------------------------------------------------
    # Defaults
    # -------------------------------------------------------------------------
    def _default_ports(self):
        return comports()

    def _default_parity(self):
        return 'None'

    def _default_port(self):
        if self.ports:
            return self.ports[0].device
        return ""

    def refresh(self):
        self.ports = self._default_ports()


class SerialTransport(RawFdTransport):

    #: Default config
    config = Instance(SerialConfig, ()).tag(config=True)

    #: Connection port
    connection = Instance(SerialPort)

    #: Whether a serial connection spools depends on the device (configuration)
    always_spools = set_default(False)

    def connect(self):
        config = self.config
        self.device_path = config.port

        if not config.port:
            raise IOError("No serial port configured! Please select a port "
                          "in Device Setup.")

        try:
            #: Save a reference
            self.protocol.transport = self

            #: Make the wrapper
            self._protocol = RawFdProtocol(self, self.protocol)

            self.connection = SerialPort(
                self._protocol,
                config.port,
                reactor,
                baudrate=config.baudrate,
                bytesize=config.bytesize,
                parity=SERIAL_PARITIES[config.parity],
                stopbits=config.stopbits,
                xonxoff=config.xonxoff,
                rtscts=config.rtscts
            )

            # Twisted is missing this
            if config.dsrdtr:
                try:
                    self.connection._serial.dsrdtr = True
                except AttributeError as e:
                    log.warning("{} | dsrdtr is not supported {}".format(
                        config.port, e))

            # Verify the underlying serial port is actually open
            try:
                ser = self.connection._serial
                if not ser.is_open:
                    raise IOError(
                        "Serial port {} failed to open".format(config.port))
                log.info("{} | opened (baudrate={}, bytesize={}, parity={}, "
                         "stopbits={}, xonxoff={}, rtscts={}, dsrdtr={})".format(
                             config.port, config.baudrate, config.bytesize,
                             config.parity, config.stopbits, config.xonxoff,
                             config.rtscts, config.dsrdtr))
            except AttributeError:
                # Fallback if _serial is not accessible
                log.info("{} | opened".format(config.port))
        except serial.SerialException as e:
            log.error("{} | Serial error: {}".format(config.port, e))
            raise
        except Exception as e:
            #: Make sure to log any issues as these tracebacks can get
            #: squashed by twisted
            log.error("{} | {}".format(config.port, traceback.format_exc()))
            raise

    def write(self, data):
        """ Write data and flush to ensure it is actually sent to the
        device immediately rather than sitting in a buffer.
        """
        if not self.connection:
            raise IOError("{} is not opened".format(self.device_path))
        if hasattr(data, 'encode'):
            data = data.encode()
        try:
            self.connection.write(data)
            # Force flush the underlying serial port buffer to ensure
            # data is physically transmitted
            try:
                self.connection._serial.flush()
            except (AttributeError, serial.SerialException):
                pass
            self.last_write = data
            log.debug("-> {} | {}".format(self.device_path, data))
        except serial.SerialException as e:
            self.connected = False
            log.error("-> {} | write FAILED (connection lost?): {}".format(
                self.device_path, e))
            raise
        except Exception as e:
            log.error("-> {} | write FAILED: {}".format(
                self.device_path, e))
            raise

    def disconnect(self):
        if self.connection:
            # Flush any remaining data before closing
            try:
                self.connection._serial.flush()
            except (AttributeError, serial.SerialException):
                pass
            log.info("{} | closed by request".format(self.device_path))
            self.connection.loseConnection()
            self.connection = None


class SerialPlugin(Plugin):
    """ Plugin for handling serial port communication

    """

    # -------------------------------------------------------------------------
    # SerialPlugin API
    # -------------------------------------------------------------------------
