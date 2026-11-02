// The libraries LNReader plugins require(), bundled into one browser script by
// scripts/update_lnreader.py (esbuild, IIFE with --global-name=__lnr_vendor, a
// `var` inside the bundle's function). The prefix maps require("cheerio") and
// friends onto it.
import * as cheerio from "cheerio";
import * as htmlparser2 from "htmlparser2";
import dayjs from "dayjs";
import { gcm } from "@noble/ciphers/aes.js";
import { parse as parseProto } from "protobufjs";

export { cheerio, htmlparser2, dayjs, gcm, parseProto };
