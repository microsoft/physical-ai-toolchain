// Copyright (c) 2026 Microsoft Corporation. All rights reserved.
// SPDX-License-Identifier: MIT
// @ts-check
import { visit } from 'unist-util-visit';

function isElement(node, tagName) {
  return Boolean(node && node.type === 'element' && node.tagName === tagName);
}

function isDataTable(node) {
  const children = node.children ?? [];
  return children.some((child) => isElement(child, 'thead'))
    || children.some((child) => isElement(child, 'th'))
    || children.some(
      (child) => (child.tagName === 'thead' || child.tagName === 'tbody')
        && (child.children ?? []).some((row) =>
          (row.children ?? []).some((cell) => cell.tagName === 'th')),
    );
}

function isPresentationTable(node) {
  const role = node.properties?.role;
  return role === 'presentation' || role === 'none';
}

function hasCaption(node) {
  return (node.children ?? []).some((child) => isElement(child, 'caption'));
}

function readDataAttribute(properties, name) {
  if (!properties) {
    return undefined;
  }

  const camel = `data${name.charAt(0).toUpperCase()}${name.slice(1)}`;
  const value = properties[`data-${name}`] ?? properties[camel];
  delete properties[`data-${name}`];
  delete properties[camel];
  return value;
}

function eachRowCell(section, callback) {
  for (const row of section.children ?? []) {
    if (!isElement(row, 'tr')) {
      continue;
    }
    for (const cell of row.children ?? []) {
      callback(cell);
    }
  }
}

function processTable(node, headingId, pageTitle) {
  node.properties ??= {};
  const captionText = readDataAttribute(node.properties, 'caption');
  const rowheader = readDataAttribute(node.properties, 'rowheader') === 'true';

  for (const section of node.children ?? []) {
    if (isElement(section, 'thead')) {
      eachRowCell(section, (cell) => {
        if (cell.tagName === 'th') {
          cell.properties ??= {};
          cell.properties.scope = 'col';
        }
      });
    } else if (isElement(section, 'tbody')) {
      eachRowCell(section, (cell) => {
        if (cell.tagName === 'th') {
          cell.properties ??= {};
          cell.properties.scope = 'row';
        }
      });
    }
  }

  if (rowheader) {
    for (const section of node.children ?? []) {
      if (!isElement(section, 'tbody')) {
        continue;
      }
      for (const row of section.children ?? []) {
        const firstCell = (row.children ?? []).find(
          (cell) => cell.tagName === 'td' || cell.tagName === 'th',
        );
        if (firstCell && firstCell.tagName === 'td') {
          firstCell.tagName = 'th';
          firstCell.properties ??= {};
          firstCell.properties.scope = 'row';
        }
      }
    }
  }

  if (captionText && !hasCaption(node)) {
    node.children = [
      {
        type: 'element',
        tagName: 'caption',
        properties: {},
        children: [{ type: 'text', value: captionText }],
      },
      ...(node.children ?? []),
    ];
  } else if (!captionText && !hasCaption(node) && headingId) {
    node.properties['aria-labelledby'] = headingId;
  } else if (!captionText && !hasCaption(node) && pageTitle) {
    node.children = [
      {
        type: 'element',
        tagName: 'caption',
        properties: {},
        children: [{ type: 'text', value: pageTitle }],
      },
      ...(node.children ?? []),
    ];
  } else if (!captionText && !hasCaption(node)) {
    throw new Error(
      'Data table has no accessible name. Add a :::table{caption="..."} directive, '
      + 'or place the table under a heading, or mark it role="presentation" if it is '
      + 'a layout table.',
    );
  }
}

export default function rehypeTableScope() {
  return (tree, file) => {
    const pageTitle = typeof file?.data?.frontMatter?.title === 'string'
      ? file.data.frontMatter.title.trim()
      : '';
    let lastHeadingId;

    visit(tree, 'element', (node) => {
      if (/^h[1-6]$/.test(node.tagName) && node.properties?.id) {
        lastHeadingId = node.properties.id;
        return;
      }
      if (node.tagName === 'table' && isDataTable(node) && !isPresentationTable(node)) {
        processTable(node, lastHeadingId, pageTitle);
      }
    });
  };
}