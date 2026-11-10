// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** A small JSON reader and writer: objects become maps, arrays lists, numbers Long or Double.
 *  The platform's org.json is not in the API stubs this app is compiled against. */
final class Json {
    private final String text;
    private int at;

    private Json(String text) {
        this.text = text;
    }

    static Object parse(String text) {
        Json reader = new Json(text);
        reader.space();
        Object value = reader.value(0);
        reader.space();
        if (reader.at != text.length()) throw new IllegalArgumentException("trailing data");
        return value;
    }

    private void space() {
        while (at < text.length() && " \t\r\n".indexOf(text.charAt(at)) >= 0) at++;
    }

    private char next() {
        if (at >= text.length()) throw new IllegalArgumentException("cut short");
        return text.charAt(at++);
    }

    private void expect(String word) {
        if (!text.startsWith(word, at)) throw new IllegalArgumentException("unexpected token");
        at += word.length();
    }

    private Object value(int depth) {
        if (depth > 32) throw new IllegalArgumentException("too deep");
        space();
        if (at >= text.length()) throw new IllegalArgumentException("cut short");
        char c = text.charAt(at);
        if (c == '{') {
            at++;
            Map<String, Object> map = new LinkedHashMap<>();
            space();
            if (at < text.length() && text.charAt(at) == '}') {
                at++;
                return map;
            }
            while (true) {
                space();
                if (next() != '"') throw new IllegalArgumentException("key expected");
                String key = string();
                space();
                if (next() != ':') throw new IllegalArgumentException("colon expected");
                map.put(key, value(depth + 1));
                space();
                char d = next();
                if (d == '}') return map;
                if (d != ',') throw new IllegalArgumentException("comma expected");
            }
        }
        if (c == '[') {
            at++;
            List<Object> list = new ArrayList<>();
            space();
            if (at < text.length() && text.charAt(at) == ']') {
                at++;
                return list;
            }
            while (true) {
                list.add(value(depth + 1));
                space();
                char d = next();
                if (d == ']') return list;
                if (d != ',') throw new IllegalArgumentException("comma expected");
            }
        }
        if (c == '"') {
            at++;
            return string();
        }
        if (c == 't') {
            expect("true");
            return Boolean.TRUE;
        }
        if (c == 'f') {
            expect("false");
            return Boolean.FALSE;
        }
        if (c == 'n') {
            expect("null");
            return null;
        }
        int start = at;
        while (at < text.length() && "+-0123456789.eE".indexOf(text.charAt(at)) >= 0) at++;
        String number = text.substring(start, at);
        if (number.isEmpty()) throw new IllegalArgumentException("value expected");
        try {
            if (number.matches("-?[0-9]{1,18}")) return Long.valueOf(number);
            return Double.valueOf(number);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("bad number");
        }
    }

    private String string() {
        StringBuilder out = new StringBuilder();
        while (true) {
            char c = next();
            if (c == '"') return out.toString();
            if (c < 0x20) throw new IllegalArgumentException("control character in a string");
            if (c != '\\') {
                out.append(c);
                continue;
            }
            char e = next();
            switch (e) {
                case '"': out.append('"'); break;
                case '\\': out.append('\\'); break;
                case '/': out.append('/'); break;
                case 'b': out.append('\b'); break;
                case 'f': out.append('\f'); break;
                case 'n': out.append('\n'); break;
                case 'r': out.append('\r'); break;
                case 't': out.append('\t'); break;
                case 'u':
                    if (at + 4 > text.length()) throw new IllegalArgumentException("cut short");
                    try {
                        out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                    } catch (NumberFormatException ex) {
                        throw new IllegalArgumentException("bad escape");
                    }
                    at += 4;
                    break;
                default: throw new IllegalArgumentException("bad escape");
            }
        }
    }

    static String write(Object value) {
        StringBuilder out = new StringBuilder();
        write(out, value);
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    private static void write(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof String) {
            quote(out, (String) value);
        } else if (value instanceof Boolean || value instanceof Long || value instanceof Integer) {
            out.append(value);
        } else if (value instanceof Double) {
            double number = (Double) value;
            if (Double.isNaN(number) || Double.isInfinite(number)) throw new IllegalArgumentException("not a JSON number");
            out.append(number);
        } else if (value instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                quote(out, entry.getKey());
                out.append(':');
                write(out, entry.getValue());
            }
            out.append('}');
        } else if (value instanceof List) {
            out.append('[');
            boolean first = true;
            for (Object item : (List<Object>) value) {
                if (!first) out.append(',');
                first = false;
                write(out, item);
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("cannot write " + value.getClass().getSimpleName());
        }
    }

    private static void quote(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c == '"' || c == '\\') out.append('\\').append(c);
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int) c));
            else out.append(c);
        }
        out.append('"');
    }
}
