# Context Budget Policy

LLM context windows are finite. MathAgent files should help agents load high-value structure without drowning in routine algebra.

Method files should be compact. Include the trigger, transformation, and follow-up moves an agent needs, but avoid long proof transcripts unless the file is an example.

Avoid storing trivial algebraic facts already known to models, such as completing the square or expanding a binomial, unless a problem has a special normalization convention.

Keep high-impact identities in `library/`. They should be small enough to load often and important enough to change a search path.

Place unfiltered Kieren notes in `library/untriaged_tagebuch/` first. Promote an item to the main library only after it has a clear trigger pattern, status, and reason to be useful.
