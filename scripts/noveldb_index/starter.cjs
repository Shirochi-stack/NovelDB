/* The resident first pass shares title tokens with the full catalogue. */
'use strict';
const fs = require('fs');
const {tokens} = require('./build.cjs');
const rows = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const postings = Object.create(null);
rows.forEach((row, number) => {
    const own = new Set([row[2], row[3], ...row[16]].flatMap(tokens));
    for (const token of own) (postings[token] || (postings[token] = [])).push(number);
});
fs.writeFileSync(process.argv[3], JSON.stringify({rows, postings, words: Object.keys(postings).sort()}));
