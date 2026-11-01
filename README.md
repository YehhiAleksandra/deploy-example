# Deploy example (demo)

Source-available demo of a zero-downtime release pipeline: a checksummed
artifact, an immutable release directory, an atomic `current` switch and a
one-command rollback.

This repository is a **view-only showcase**. See [LICENSE](LICENSE) before you
do anything with the code.

## What it does

```text
example/site/ ──build──▶ site-v1.tar + .sha256
                              │
                              ▼
        target/releases/v1/public      ← extracted, verified, health-checked
                              │
                     switch (atomic)      current  ─▶ releases/v1
                                          previous ─▶ (the release before it)
```

- `build` packs the site into a deterministic tarball and writes its SHA-256
  next to it;
- `deploy` refuses a checksum mismatch, extracts into a staging directory
  inside the target, rejects absolute paths, `..` escapes and links, checks
  the health file, then renames the staging directory into `releases/<version>`;
- the `current` pointer is switched only after everything above passed:
  on POSIX it is a symlink replaced with `os.replace`, on Windows without
  symlink privilege a `.current` pointer file stays authoritative;
- `rollback` swaps `current` and `previous`, so the last two deployments are
  always one command apart;
- `prune` keeps the newest N releases and never deletes the active one.

## Quick start

Python 3.12+ (`tarfile.extractall(filter="data")` and `Path.unlink(missing_ok=True)`).

```bash
python release.py build --version v1
python release.py deploy --target ./demo --artifact build/site-v1.tar
python release.py status --target ./demo
python release.py rollback --target ./demo
```

`--target` is any local directory; the demo does not touch a remote host.

## What is not in the demo

The production version that runs on the real servers keeps the following
private:

- SSH/rsync transport and a remote staging path;
- CI-run attestation: the deploy accepts only an artifact built by the watched
  pipeline run for the exact commit;
- live-domain health check (HTTPS status, TLS expiry, content hash) after the
  switch, with automatic rollback on failure;
- nginx config rendering and cache-busting;
- notification hooks on success and on rollback.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

Source-available, viewing and evaluation only. Copying into another product,
redistribution and commercial use are not permitted. For a commercial license,
contact the author.

---

# Деплой-пример (демо)

Демо-витрина релизного пайплайна: артефакт с контрольной суммой, неизменяемые
релизы, атомарное переключение `current` и откат одной командой.

- `build` собирает сайт в tar и считает SHA-256;
- `deploy` проверяет сумму, распаковывает архив во временный каталог и
  отклоняет пути с `..`, абсолютные пути и ссылки;
- переключение `current` происходит только после успешной проверки
  health-файла, `rollback` меняет местами `current` и `previous`;
- `prune` хранит N последних релизов и не удаляет активный.

**Чего нет в демо** (осталось в приватной версии): доставка по SSH/rsync,
аттестация CI-прогона, проверка живого домена после переключения с автооткатом,
рендер конфига nginx, уведомления.

Лицензия: исходники открыты для просмотра и ознакомления; копирование,
распространение и коммерческое использование запрещены.
