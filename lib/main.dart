import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:file_picker/file_picker.dart';

// ------------------------- Константы протокола -------------------------
// Должны точно совпадать с drop.py
const String discoverReq = 'FILESHARE_DISCOVER';
const String discoverResp = 'FILESHARE_RESPONSE ';
const int chunk = 65536;

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const DropApp());
}

// ----------------------------- Приложение ------------------------------
class DropApp extends StatelessWidget {
  const DropApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DropΩ',
      debugShowCheckedModeBanner: false,
      themeMode: ThemeMode.system, // тема следует за системой
      theme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF1f6aa5), // акцентный цвет
        brightness: Brightness.light,
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        colorSchemeSeed: const Color(0xFF1f6aa5),
        brightness: Brightness.dark,
      ),
      home: const HomePage(),
    );
  }
}

// --------------------------- Главный экран -----------------------------
class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('DropΩ'), centerTitle: true),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Text('Передача файлов по локальной сети',
                  style: theme.textTheme.titleMedium),
              const SizedBox(height: 8),
              Text('Устройства должны быть в одной сети',
                  style: theme.textTheme.bodySmall
                      ?.copyWith(color: theme.hintColor)),
              const SizedBox(height: 32),
              FilledButton.icon(
                icon: const Icon(Icons.upload_file),
                label: const Text('Поделиться файлами'),
                style: FilledButton.styleFrom(
                    minimumSize: const Size(300, 52)),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const SharePage()),
                ),
              ),
              const SizedBox(height: 16),
              FilledButton.icon(
                icon: const Icon(Icons.download),
                label: const Text('Получить файлы'),
                style: FilledButton.styleFrom(
                    minimumSize: const Size(300, 52)),
                onPressed: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const ReceivePage()),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ------------------------- Вспомогательные функции ---------------------
String humanSize(int n) {
  double size = n.toDouble();
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'];
  int i = 0;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return i == 0 ? 'n Б' : '${size.toStringAsFixed(1)} ${units[i]}';
}

Future<List<String>> getLocalIPs() async {
  final ips = <String>{};
  try {
    final nis = await NetworkInterface.list(type: InternetAddressType.IPv4);
    for (final ni in nis) {
      for (final a in ni.addresses) {
        if (!a.isLoopback) ips.add(a.address);
      }
    }
  } catch (_) {}
  return ips.toList();
}

List<String> broadcastAddresses(List<String> ips) {
  final addrs = <String>{'255.255.255.255'};
  for (final ip in ips) {
    final parts = ip.split('.');
    if (parts.length == 4) {
      addrs.add('${parts[0]}.${parts[1]}.${parts[2]}.255');
      addrs.add('${parts[0]}.${parts[1]}.255.255');
      addrs.add('${parts[0]}.255.255.255');
    }
  }
  return addrs.toList();
}

String defaultSaveDir() {
  final home = Platform.isWindows
      ? (Platform.environment['USERPROFILE'] ?? '.')
      : (Platform.environment['HOME'] ?? '.');
  return '$home${Platform.pathSeparator}Downloads';
}

String uniquePath(String path) {
  if (!File(path).existsSync()) return path;
  final idx = path.lastIndexOf('.');
  final base = idx > 0 ? path.substring(0, idx) : path;
  final ext = idx > 0 ? path.substring(idx) : '';
  int i = 1;
  while (true) {
    final candidate = '$base ($i)$ext';
    if (!File(candidate).existsSync()) return candidate;
    i++;
  }
}

// --------------------- Буферизованное чтение из сокета ----------------
class BufferedReader {
  BufferedReader(this._stream) {
    _it = StreamIterator<List<int>>(_stream);
  }

  final Stream<List<int>> _stream;
  late final StreamIterator<List<int>> _it;
  final List<int> _buf = <int>[];

  Future<List<int>> readExactly(int n) async {
    while (_buf.length < n) {
      final has = await _it.moveNext();
      if (!has) {
        throw const SocketException('Соединение закрыто');
      }
      _buf.addAll(_it.current);
    }
    final out = _buf.sublist(0, n);
    _buf.removeRange(0, n);
    return out;
  }
}

// ------------------------------ Отправитель ----------------------------
class Sender {
  Sender(this.port, this.files, this.onStatus, this.onProgress);

  final int port;
  final List<String> files;
  final void Function(String) onStatus;
  final void Function(double) onProgress;

  ServerSocket? _tcp;
  RawDatagramSocket? _udp;
  HttpServer? _http;

  List<Map<String, dynamic>> fileInfo() => files.map((p) {
        final f = File(p);
        return {
          'name': p.split(RegExp(r'[/\\]')).last,
          'size': f.lengthSync(),
        };
      }).toList();

  Future<void> _startUdpResponder() async {
    if (_udp != null) return;
    _udp = await RawDatagramSocket.bind(InternetAddress.anyIPv4, port);
    _udp!.broadcastEnabled = true;
    _udp!.listen((event) {
      if (event != RawSocketEvent.read) return;
      Datagram? dg;
      while (true) {
        dg = _udp!.receive();
        if (dg == null) break;
        if (String.fromCharCodes(dg.data) == discoverReq) {
          final info = jsonEncode(
              {'host': Platform.localHostname, 'files': fileInfo()});
          _udp!.send(utf8.encode(discoverResp + info), dg.address, dg.port);
        }
      }
    });
  }

  Future<void> startTcp() async {
    await _startUdpResponder();
    _tcp = await ServerSocket.bind(InternetAddress.anyIPv4, port);
    _tcp!.listen(_handleClient);
    onStatus('Раздача (TCP) запущена. Порт: $port');
  }

  Future<void> _handleClient(Socket sock) async {
    try {
      final reader = BufferedReader(sock);
      final info = jsonEncode(
          {'host': Platform.localHostname, 'files': fileInfo()});
      final bytes = utf8.encode(info);
      final header = Uint8List(4);
      ByteData.view(header.buffer).setUint32(0, bytes.length, Endian.big);
      sock.add(header);
      sock.add(bytes);
      await sock.flush();

      final ok = await reader.readExactly(2);
      if (utf8.decode(ok) != 'OK') {
        await sock.close();
        return;
      }
      onStatus('Клиент начал загрузку');
      final total = files.fold<int>(0, (s, p) => s + File(p).lengthSync());
      int sent = 0;
      for (final p in files) {
        final raf = File(p).openSync();
        try {
          while (true) {
            final data = raf.readSync(chunk);
            if (data.isEmpty) break;
            sock.add(data);
            await sock.flush();
            sent += data.length;
            onProgress(total == 0 ? 0 : sent / total);
          }
        } finally {
          raf.closeSync();
        }
      }
      onStatus('Передача завершена');
    } catch (e) {
      onStatus('Ошибка передачи: $e');
    } finally {
      try {
        await sock.close();
      } catch (_) {}
    }
  }

  Future<void> startHttp() async {
    await _startUdpResponder();
    final nameMap = buildNameMap(files);
    _http = await HttpServer.bind(InternetAddress.anyIPv4, port);
    _http!.listen((req) async {
      try {
        final path =
            Uri.decodeComponent(req.uri.path).replaceFirst(RegExp(r'^/'), '');
        if (path.isEmpty || path == 'index.html') {
          serveIndex(req, nameMap);
        } else if (nameMap.containsKey(path)) {
          serveFile(req, nameMap[path]!);
        } else {
          req.response.statusCode = HttpStatus.notFound;
          req.response.close();
        }
      } catch (_) {
        req.response.statusCode = HttpStatus.internalServerError;
        req.response.close();
      }
    });
    onStatus('HTTP-сервер запущен. Порт: $port');
  }

  Future<void> stop() async {
    await _tcp?.close();
    _udp?.close();
    await _http?.close(force: true);
    _tcp = null;
    _udp = null;
    _http = null;
  }
}

Map<String, String> buildNameMap(List<String> files) {
  final map = <String, String>{};
  final used = <String>{};
  for (final p in files) {
    final base = p.split(RegExp(r'[/\\]')).last;
    var name = base;
    int i = 1;
    while (used.contains(name)) {
      final dot = base.lastIndexOf('.');
      final stem = dot > 0 ? base.substring(0, dot) : base;
      final ext = dot > 0 ? base.substring(dot) : '';
      name = '${stem}_$i$ext';
      i++;
    }
    used.add(name);
    map[name] = p;
  }
  return map;
}

void serveIndex(HttpRequest req, Map<String, String> nameMap) {
  final b = StringBuffer()
    ..write('<html><head><meta charset="utf-8"><title>Файлы</title>')
    ..write('<style>body{font-family:sans-serif;max-width:760px;'
        'margin:40px auto;padding:0 16px}'
        'h2{border-bottom:2px solid #1a73e8;padding-bottom:8px}'
        'a{display:block;padding:10px 0;color:#1a73e8;'
        'text-decoration:none;border-bottom:1px solid #eee}'
        '.s{color:#888;margin-left:10px}</style></head><body>')
    ..write('<h2>Доступные файлы</h2>');
  nameMap.forEach((name, path) {
    final size = humanSize(File(path).lengthSync());
    b.write('<div><a href="/${Uri.encodeComponent(name)}">$name</a>'
        '<span class="s">$size</span></div>');
  });
  b.write('</body></html>');
  final body = utf8.encode(b.toString());
  req.response.statusCode = HttpStatus.ok;
  req.response.headers.set(
      HttpHeaders.contentTypeHeader, 'text/html; charset=utf-8');
  req.response.headers.set(HttpHeaders.contentLengthHeader, body.length);
  req.response.add(body);
  req.response.close();
}

void serveFile(HttpRequest req, String path) {
  final f = File(path);
  final size = f.lengthSync();
  final name = path.split(RegExp(r'[/\\]')).last;
  req.response.statusCode = HttpStatus.ok;
  req.response.headers.set(HttpHeaders.contentTypeHeader, contentType(name));
  req.response.headers.set(HttpHeaders.contentLengthHeader, size);
  req.response.headers
      .set('Content-Disposition', 'attachment; filename="$name"');
  req.response.addStream(f.openRead());
  req.response.close();
}

String contentType(String name) {
  final ext = name.split('.').last.toLowerCase();
  switch (ext) {
    case 'txt':
      return 'text/plain; charset=utf-8';
    case 'html':
    case 'htm':
      return 'text/html; charset=utf-8';
    case 'json':
      return 'application/json';
    case 'png':
      return 'image/png';
    case 'jpg':
    case 'jpeg':
      return 'image/jpeg';
    case 'gif':
      return 'image/gif';
    case 'pdf':
      return 'application/pdf';
    case 'zip':
      return 'application/zip';
    case 'mp3':
      return 'audio/mpeg';
    case 'mp4':
      return 'video/mp4';
    default:
      return 'application/octet-stream';
  }
}

// ------------------------------- Получатель ----------------------------
Future<List<Map<String, dynamic>>> discover(
  int port, {
  Duration timeout = const Duration(seconds: 2),
}) async {
  final ips = await getLocalIPs();
  final sock = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
  sock.broadcastEnabled = true;
  final req = utf8.encode(discoverReq);
  for (final b in broadcastAddresses(ips)) {
    try {
      sock.send(req, InternetAddress(b), port);
    } catch (_) {}
  }
  final results = <String, Map<String, dynamic>>{};
  final sub = sock.listen((event) {
    if (event != RawSocketEvent.read) return;
    Datagram? dg;
    while (true) {
      dg = sock.receive();
      if (dg == null) break;
      final txt = utf8.decode(dg.data, allowMalformed: true);
      if (txt.startsWith(discoverResp)) {
        try {
          final payload = jsonDecode(txt.substring(discoverResp.length));
          if (payload is Map<String, dynamic>) {
            payload['ip'] = dg.address.address;
            results[dg.address.address] = payload;
          }
        } catch (_) {}
      }
    }
  });
  await Future.delayed(timeout);
  await sub.cancel();
  sock.close();
  return results.values.toList();
}

Future<int> receiveFiles({
  required String host,
  required int port,
  required String saveDir,
  required void Function(String) onStatus,
  required void Function(double) onProgress,
}) async {
  final sock = await Socket.connect(host, port);
  try {
    final reader = BufferedReader(sock);
    final lenBytes = await reader.readExactly(4);
    final bd = ByteData.view(Uint8List.fromList(lenBytes).buffer);
    final len = bd.getUint32(0, Endian.big);
    final jsonBytes = await reader.readExactly(len);
    final info = jsonDecode(utf8.decode(jsonBytes)) as Map<String, dynamic>;
    final files = (info['files'] as List).cast<Map<String, dynamic>>();

    sock.add(utf8.encode('OK'));
    await sock.flush();

    final dir = Directory(saveDir);
    if (!dir.existsSync()) dir.createSync(recursive: true);

    final total =
        files.fold<int>(0, (s, f) => s + (f['size'] as num).toInt());
    int done = 0;
    int count = 0;
    for (final f in files) {
      final rawName = (f['name'] as String).split(RegExp(r'[/\\]')).last;
      final name = rawName.isEmpty ? 'file' : rawName;
      final size = (f['size'] as num).toInt();
      final dest = uniquePath('$saveDir${Platform.pathSeparator}$name');
      final sink = File(dest).openWrite();
      int remaining = size;
      while (remaining > 0) {
        final n = remaining < chunk ? remaining : chunk;
        final data = await reader.readExactly(n);
        sink.add(data);
        remaining -= n;
        done += n;
        onProgress(total == 0 ? 0 : done / total);
      }
      await sink.close();
      count++;
      onStatus('Сохранено: ${dest.split(Platform.pathSeparator).last}');
    }
    return count;
  } finally {
    try {
      sock.destroy();
    } catch (_) {}
  }
}

// ------------------------------ UI: Поделиться -------------------------
class SharePage extends StatefulWidget {
  const SharePage({super.key});

  @override
  State<SharePage> createState() => _SharePageState();
}

class _SharePageState extends State<SharePage> {
  final List<String> _files = [];
  final _portCtrl = TextEditingController(text: '5000');
  final _pathCtrl = TextEditingController();
  Sender? _sender;
  bool _running = false;
  double _progress = 0;
  String _status = '';

  @override
  void dispose() {
    _sender?.stop();
    _portCtrl.dispose();
    _pathCtrl.dispose();
    super.dispose();
  }

  Future<void> _pickFiles() async {
    // v13: статический метод, возвращает List<PlatformFile>
    final picked = await FilePicker.pickFiles(type: FileType.any);
    if (picked.isEmpty) return; // пользователь отменил выбор
    for (final f in picked) {
      final path = f.path;
      if (path != null && !_files.contains(path)) {
        _files.add(path);
      }
    }
    if (mounted) setState(() {});
  }

  void _addPath() {
    final path = _pathCtrl.text.trim();
    if (path.isEmpty) return;
    final f = File(path);
    if (!f.existsSync()) {
      _setStatus('Файл/папка не найдены: $path');
      return;
    }
    if (f.statSync().type == FileSystemEntityType.directory) {
      final entries = Directory(path).listSync().whereType<File>();
      for (final e in entries) {
        if (!_files.contains(e.path)) _files.add(e.path);
      }
    } else {
      if (!_files.contains(path)) _files.add(path);
    }
    _pathCtrl.clear();
    setState(() {});
  }

  int? _parsePort() {
    final p = int.tryParse(_portCtrl.text.trim());
    if (p == null || p < 1 || p > 65535) return null;
    return p;
  }

  Future<void> _startTcp() async {
    final port = _parsePort();
    if (port == null) {
      _setStatus('Введите корректный порт (1–65535)');
      return;
    }
    if (_files.isEmpty) {
      _setStatus('Сначала выберите файлы');
      return;
    }
    await _stop();
    final s = Sender(port, List.of(_files), _setStatus, _setProgress);
    _sender = s;
    setState(() => _running = true);
    try {
      await s.startTcp();
      final ips = await getLocalIPs();
      _setStatus('Раздача (TCP) запущена. Порт: $port\n'
          'IP: ${ips.isEmpty ? "не определены" : ips.join(", ")}');
    } catch (e) {
      setState(() => _running = false);
      _setStatus('Ошибка запуска: $e');
    }
  }

  Future<void> _startHttp() async {
    final port = _parsePort();
    if (port == null) {
      _setStatus('Введите корректный порт (1–65535)');
      return;
    }
    if (_files.isEmpty) {
      _setStatus('Сначала выберите файлы');
      return;
    }
    await _stop();
    final s = Sender(port, List.of(_files), _setStatus, _setProgress);
    _sender = s;
    setState(() => _running = true);
    try {
      await s.startHttp();
      final ips = await getLocalIPs();
      final links = ips.map((ip) => 'http://$ip:$port/').join('\n');
      _setStatus('HTTP-раздача. Откройте в браузере:\n'
          '${links.isEmpty ? "http://127.0.0.1:$port/" : links}');
    } catch (e) {
      setState(() => _running = false);
      _setStatus('Ошибка запуска: $e');
    }
  }

  Future<void> _stop() async {
    await _sender?.stop();
    _sender = null;
    if (mounted) {
      setState(() {
        _running = false;
        _progress = 0;
      });
    }
  }

  void _setStatus(String text) {
    if (mounted) setState(() => _status = text);
  }

  void _setProgress(double v) {
    if (mounted) setState(() => _progress = v);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Поделиться файлами')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FilledButton.icon(
            icon: const Icon(Icons.folder_open),
            label: const Text('Выбрать файлы…'),
            onPressed: _pickFiles,
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _pathCtrl,
            decoration: InputDecoration(
              labelText: '…или введите путь к файлу/папке',
              border: const OutlineInputBorder(),
              suffixIcon: IconButton(
                icon: const Icon(Icons.add),
                onPressed: _addPath,
              ),
            ),
            onSubmitted: (_) => _addPath(),
          ),
          const SizedBox(height: 16),
          Text('Выбранные файлы (${_files.length})',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          if (_files.isEmpty)
            const Text('Файлы не выбраны',
                style: TextStyle(color: Colors.grey))
          else
            ..._files.map((p) {
              final name = p.split(RegExp(r'[/\\]')).last;
              final size = humanSize(File(p).lengthSync());
              return ListTile(
                dense: true,
                leading: const Icon(Icons.insert_drive_file),
                title: Text(name),
                subtitle: Text(size),
                trailing: IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () {
                    setState(() => _files.remove(p));
                  },
                ),
              );
            }),
          const SizedBox(height: 16),
          TextField(
            controller: _portCtrl,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(
              labelText: 'Порт',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton(
            onPressed: _running ? null : _startTcp,
            child: const Text('Начать раздачу (TCP, через программу)'),
          ),
          const SizedBox(height: 8),
          FilledButton.tonal(
            onPressed: _running ? null : _startHttp,
            child: const Text('Поделиться по HTTP (через браузер)'),
          ),
          const SizedBox(height: 8),
          OutlinedButton(
            onPressed: _running ? _stop : null,
            child: const Text('Остановить'),
          ),
          const SizedBox(height: 16),
          LinearProgressIndicator(value: _progress),
          const SizedBox(height: 8),
          Text(_status),
        ],
      ),
    );
  }
}

// ------------------------------ UI: Получить ---------------------------
class ReceivePage extends StatefulWidget {
  const ReceivePage({super.key});

  @override
  State<ReceivePage> createState() => _ReceivePageState();
}

class _ReceivePageState extends State<ReceivePage> {
  final _portCtrl = TextEditingController(text: '5000');
  final _ipCtrl = TextEditingController();
  final _saveCtrl = TextEditingController(text: defaultSaveDir());
  bool _auto = true;
  bool _busy = false;
  double _progress = 0;
  String _status = '';
  List<Map<String, dynamic>> _senders = [];

  @override
  void dispose() {
    _portCtrl.dispose();
    _ipCtrl.dispose();
    _saveCtrl.dispose();
    super.dispose();
  }

  int? _parsePort() {
    final p = int.tryParse(_portCtrl.text.trim());
    if (p == null || p < 1 || p > 65535) return null;
    return p;
  }

  Future<void> _pickDir() async {
    // v13: статический метод, возвращает String?
    final path = await FilePicker.getDirectoryPath();
    if (path != null && mounted) {
      setState(() => _saveCtrl.text = path);
    }
  }

  Future<void> _onAction() async {
    final port = _parsePort();
    if (port == null) {
      _setStatus('Введите корректный порт (1–65535)');
      return;
    }
    if (_auto) {
      setState(() => _busy = true);
      _setStatus('Поиск отправителей…');
      try {
        final res = await discover(port);
        setState(() {
          _senders = res;
          _busy = false;
        });
        _setStatus(res.isEmpty
            ? 'Отправители не найдены. Проверьте порт и сеть.'
            : 'Найдено отправителей: ${res.length}');
      } catch (e) {
        setState(() => _busy = false);
        _setStatus('Ошибка: $e');
      }
    } else {
      final host = _ipCtrl.text.trim();
      if (host.isEmpty) {
        _setStatus('Введите IP-адрес отправителя');
        return;
      }
      _startReceive(host, port);
    }
  }

  Future<void> _startReceive(String host, int port) async {
    setState(() => _busy = true);
    _setStatus('Подключение к $host:$port…');
    try {
      final count = await receiveFiles(
        host: host,
        port: port,
        saveDir: _saveCtrl.text.trim(),
        onStatus: _setStatus,
        onProgress: (v) {
          if (mounted) setState(() => _progress = v);
        },
      );
      _setStatus('Готово! Получено файлов: $count');
    } catch (e) {
      _setStatus('Ошибка: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _setStatus(String text) {
    if (mounted) setState(() => _status = text);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Получить файлы')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Как найти отправителя?',
              style: Theme.of(context).textTheme.titleSmall),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: true, label: Text('Автоматически')),
              ButtonSegment(value: false, label: Text('Вручную')),
            ],
            selected: {_auto},
            onSelectionChanged: (s) => setState(() => _auto = s.first),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _portCtrl,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(
                    labelText: 'Порт',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              if (!_auto) ...[
                const SizedBox(width: 12),
                Expanded(
                  child: TextField(
                    controller: _ipCtrl,
                    decoration: const InputDecoration(
                      labelText: 'IP-адрес',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
              ],
            ],
          ),
          const SizedBox(height: 12),
          FilledButton(
            onPressed: _busy ? null : _onAction,
            child: Text(_auto ? 'Найти отправителей' : 'Подключиться'),
          ),
          const SizedBox(height: 16),
          if (_auto && _senders.isNotEmpty) ...[
            Text('Найденные отправители:',
                style: Theme.of(context).textTheme.titleSmall),
            ..._senders.map((s) {
              final files = (s['files'] as List).cast<Map<String, dynamic>>();
              final total =
                  files.fold<int>(0, (x, f) => x + (f['size'] as num).toInt());
              return Card(
                child: ListTile(
                  leading: const Icon(Icons.computer),
                  title: Text('${s['ip']} — ${s['host'] ?? ''}'),
                  subtitle:
                      Text('файлов: ${files.length} · ${humanSize(total)}'),
                  onTap: () => _startReceive(
                      s['ip'] as String, _parsePort() ?? 5000),
                ),
              );
            }),
          ],
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _saveCtrl,
                  decoration: const InputDecoration(
                    labelText: 'Папка сохранения',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              IconButton(
                icon: const Icon(Icons.folder_open),
                onPressed: _pickDir,
              ),
            ],
          ),
          const SizedBox(height: 16),
          LinearProgressIndicator(value: _progress),
          const SizedBox(height: 8),
          Text(_status),
        ],
      ),
    );
  }
}
