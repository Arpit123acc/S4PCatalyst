#!/usr/bin/perl
use strict;
open my $fh, '<:raw', 'C:/Users/arpit.c.srivastava/Downloads/S4PC-Catalyst-v1.0/input/FD Test AI Stock Monitoring.docx.md' or die $!;
my $data = do { local $/; <$fh> };
close $fh;
while ($data =~ /([\x20-\x7e]{4,})/g) { print "$1\n"; }
