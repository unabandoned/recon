'use strict';
/*
 * The browser half of `recon.compare`, over `package.json` rather than lockfiles.
 *
 * This is a second implementation, which this repository normally treats as a
 * defect. It is allowed here on one condition, enforced by
 * `tests/test_crosscheck.py`: it must produce **byte-identical JSON** to the
 * Python `compare()` for the same inputs. A second implementation that is
 * proven to agree is a cross-check; one that is merely believed to agree is the
 * bug factory recon exists to catch. If you change either side, the test fails
 * until you change both.
 *
 * It reads manifests, not lockfiles, and that is a deliberate limit rather than
 * an accident of what is easy. A manifest is JSON in every ecosystem, while
 * pnpm and yarn lockfiles are YAML and bun's is binary — so reading manifests
 * needs no parser, no vendored dependency, and no build step, and it works for
 * repositories that commit no lockfile at all. What it cannot answer is what
 * actually got installed. Those fields come out empty rather than guessed, and
 * the page says so.
 */
(function (global) {
  var PEER = /\(.*\)$/;
  var NUMERIC = /^\d+\.\d+\.\d+.*$/;
  var NAME = /^[A-Za-z0-9._-]+$/;
  // Slashes are legal in refs (`feature/x`), so `..` is excluded by name —
  // otherwise `owner/repo@../..` walks the raw URL somewhere else.
  var REF = /^[A-Za-z0-9._/-]+$/;

  function byCodepoint(x, y) { return x < y ? -1 : x > y ? 1 : 0; }

  // `@scope/name@1.2.3` -> ['@scope/name', '1.2.3']. The peer suffix comes off
  // first or the split lands inside `(vue@3.3.4)`.
  function splitIdent(ident) {
    ident = String(ident).replace(PEER, '');
    var at = ident.lastIndexOf('@');
    if (at <= 0) return [ident, ''];
    return [ident.slice(0, at), ident.slice(at + 1)];
  }

  function aliasTarget(specifier) {
    var spec = String(specifier || '').trim();
    if (spec.indexOf('npm:') !== 0) return '';
    return splitIdent(spec.slice(4))[0];
  }

  function isPinned(specifier) {
    return Boolean(specifier) && NUMERIC.test(specifier);
  }

  function basename(name) {
    return name.charAt(0) === '@' && name.indexOf('/') > -1
      ? name.slice(name.indexOf('/') + 1) : name;
  }

  function scopeOf(name) {
    return name.charAt(0) === '@' && name.indexOf('/') > -1
      ? name.slice(0, name.indexOf('/')) : '';
  }

  function parseRepo(value) {
    var text = String(value || '').trim().replace(/\/+$/, '');
    var ref = null;
    if (text.indexOf('git@github.com:') === 0) text = text.slice('git@github.com:'.length);
    var prefixes = ['https://github.com/', 'http://github.com/', 'github.com/'];
    for (var i = 0; i < prefixes.length; i++) {
      if (text.indexOf(prefixes[i]) === 0) { text = text.slice(prefixes[i].length); break; }
    }
    if (/\.git$/.test(text)) text = text.slice(0, -4);
    if (text.indexOf('/tree/') > -1) {
      var cut = text.indexOf('/tree/');
      ref = text.slice(cut + 6);
      text = text.slice(0, cut);
    } else if ((text.match(/@/g) || []).length === 1 && text.charAt(0) !== '@') {
      var at = text.indexOf('@');
      ref = text.slice(at + 1);
      text = text.slice(0, at);
    }
    var parts = text.split('/').filter(Boolean);
    if (parts.length < 2) throw new Error('cannot read an owner/repo out of ' + JSON.stringify(value));
    if (!NAME.test(parts[0]) || !NAME.test(parts[1])) {
      throw new Error(JSON.stringify(value) + ' does not name a GitHub repository');
    }
    if (ref !== null && (!REF.test(ref) || ref.split('/').indexOf('..') > -1)) {
      throw new Error(JSON.stringify(ref) + ' is not a usable git ref');
    }
    return { owner: parts[0], repo: parts[1], ref: ref };
  }

  // package.json -> the same shape `lockfile.read_manifest` produces.
  function readManifest(doc) {
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
      throw new Error('package.json is not an object');
    }
    var direct = {};
    [['dependencies', false], ['devDependencies', true]].forEach(function (pair) {
      var entries = doc[pair[0]];
      if (!entries || typeof entries !== 'object') return;
      Object.keys(entries).forEach(function (name) {
        var spec = String(entries[name]);
        direct[name] = {
          name: name, specifier: spec, version: '', dev: pair[1],
          resolved_name: aliasTarget(spec)
        };
      });
    });
    return { tool: 'manifest', lockfile_version: '', direct: direct, resolved: {} };
  }

  function pkg(dep) { return dep.resolved_name || dep.name; }

  function depRow(dep) {
    return {
      package: dep.name, version: dep.version, specifier: dep.specifier,
      dev: dep.dev, pinned: isPinned(dep.specifier)
    };
  }

  function side(lf) {
    var names = Object.keys(lf.direct);
    var pinned = 0, runtime = 0;
    names.forEach(function (n) {
      if (isPinned(lf.direct[n].specifier)) pinned++;
      if (!lf.direct[n].dev) runtime++;
    });
    return {
      tool: lf.tool, lockfile_version: lf.lockfile_version,
      direct: names.length, runtime: runtime,
      packages: Object.keys(lf.resolved).length, pinned: pinned
    };
  }

  function compare(baseline, subject) {
    var a = baseline.direct, b = subject.direct;
    var aNames = Object.keys(a), bNames = Object.keys(b);

    var addedNames = bNames.filter(function (n) { return !(n in a); }).sort(byCodepoint);
    var removedNames = aNames.filter(function (n) { return !(n in b); }).sort(byCodepoint);
    var shared = aNames.filter(function (n) { return n in b; }).sort(byCodepoint);

    var replaced = [];
    var byBaseRemoved = {}, byBaseAdded = {};
    removedNames.forEach(function (n) { byBaseRemoved[basename(n)] = n; });
    addedNames.forEach(function (n) { byBaseAdded[basename(n)] = n; });
    Object.keys(byBaseRemoved).filter(function (base) {
      return base in byBaseAdded;
    }).sort(byCodepoint).forEach(function (base) {
      var was = byBaseRemoved[base], now = byBaseAdded[base];
      if (was === now) return;
      replaced.push({
        package: base, was: was, now: now,
        was_version: a[was].version, now_version: b[now].version,
        into_scope: scopeOf(now), out_of_scope: scopeOf(was), via_alias: false
      });
    });
    shared.forEach(function (name) {
      if (pkg(a[name]) === pkg(b[name])) return;
      replaced.push({
        package: name, was: pkg(a[name]), now: pkg(b[name]),
        was_version: a[name].version, now_version: b[name].version,
        into_scope: scopeOf(pkg(b[name])), out_of_scope: scopeOf(pkg(a[name])),
        via_alias: true
      });
    });

    var swapped = {}, aliasedKeys = {};
    replaced.forEach(function (r) {
      swapped[r.was] = true; swapped[r.now] = true;
      if (r.via_alias) aliasedKeys[r.package] = true;
    });

    var added = addedNames.filter(function (n) { return !(n in swapped); })
      .map(function (n) { return depRow(b[n]); });
    var removed = removedNames.filter(function (n) { return !(n in swapped); })
      .map(function (n) { return depRow(a[n]); });

    var pinning = [];
    shared.forEach(function (name) {
      if (name in aliasedKeys) return;
      // Version deltas need resolved versions, which a manifest does not have.
      // The section stays empty rather than being filled with specifier text
      // dressed up as a version.
      var oldPinned = isPinned(a[name].specifier), newPinned = isPinned(b[name].specifier);
      if (oldPinned !== newPinned) {
        pinning.push({
          package: name, from: a[name].specifier, to: b[name].specifier,
          direction: newPinned ? 'pinned' : 'unpinned'
        });
      }
    });

    var byKind = { major: 0, minor: 0, patch: 0, downgrade: 0, changed: 0 };
    return {
      baseline: side(baseline), subject: side(subject),
      direct: {
        added: added, removed: removed, replaced: replaced,
        bumped: [], pinning: pinning
      },
      tree: { added: [], removed: [], multi_version: [] },
      totals: {
        direct_baseline: aNames.length, direct_subject: bNames.length,
        added: added.length, removed: removed.length, replaced: replaced.length,
        bumped: 0,
        bumped_major: byKind.major, bumped_minor: byKind.minor,
        bumped_patch: byKind.patch, bumped_downgrade: byKind.downgrade,
        bumped_changed: byKind.changed,
        pinning: pinning.length,
        tree_baseline: 0, tree_subject: 0,
        tree_added: 0, tree_removed: 0, multi_version: 0
      }
    };
  }

  function headline(diff) {
    var t = diff.totals, bits = [];
    if (t.added) bits.push(t.added + ' added');
    if (t.removed) bits.push(t.removed + ' dropped');
    if (t.replaced) bits.push(t.replaced + ' replaced with a scoped republish');
    if (t.bumped) {
      bits.push(t.bumped + ' bumped' + (t.bumped_major ? ' (' + t.bumped_major + ' major)' : ''));
    }
    if (t.bumped_downgrade) bits.push(t.bumped_downgrade + ' downgraded');
    if (t.pinning) bits.push(t.pinning + ' pinning change(s)');
    if (!bits.length) return 'The two sides declare identical direct dependencies.';
    var lead = 'Against the baseline, the subject has ' + bits.join(', ');
    // No lockfile was read, so there is no tree. "0 packages in the resolved
    // tree" would read as a measurement rather than an absence.
    if (!t.tree_baseline && !t.tree_subject) return lead + '.';
    var delta = t.tree_subject - t.tree_baseline;
    return lead + ' — and ' + (delta > 0 ? '+' : '') + delta +
      ' package(s) in the resolved tree (' + t.tree_baseline + ' → ' + t.tree_subject + ').';
  }

  var api = {
    parseRepo: parseRepo, readManifest: readManifest, compare: compare,
    headline: headline, splitIdent: splitIdent, aliasTarget: aliasTarget,
    isPinned: isPinned, basename: basename
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else global.reconCompare = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
