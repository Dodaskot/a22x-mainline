/* SPDX-License-Identifier: MIT
 * Every color the interface uses lives here. Daylight and Aurora take their tones
 * from tools/oklch.py and their backdrops and shadow from tools/theme_assets.c.
 * Tile tables follow the home screen: Backup, Files, Restore, Verify, Storage, Power.
 */
#ifndef A22X_THEME_H
#define A22X_THEME_H

#include <stdbool.h>
#include <stdint.h>
#include "theme_assets.h"

/* ANSI colors for a light terminal, in the order of the model's dark palette. Bright
 * pastel text cannot be read on white, so each entry is a darker partner. */
static const uint32_t terminal_light_palette[16]={0x1c1c2eu,0xc51d28u,0x1a7f37u,0x8a5a00u,0x0a5bd3u,0x8632b6u,0x00717fu,0x58586eu,
    0x6e6e82u,0xd0303cu,0x22863au,0x9a6a00u,0x2f6fdeu,0xa23bd0u,0x1b7c83u,0x1c1c2eu};
typedef struct {
    const char *code,*name;
    uint32_t bg,panel,panel2,line,edge,text,muted,accent,container,container_edge,on_accent,icon_fill,knob;
    uint32_t green,red,blue,purple,danger,toast_fill,toast_text,shadow,hero_top,hero_bottom,hero_text,hero_sub;
    uint32_t tile_fill[6],tile_ink[6];
    uint32_t terminal_bg,terminal_fg;const uint32_t *terminal_palette;
    const uint8_t *backdrop;
    unsigned int backdrop_w,backdrop_h; /* 0 means the size of the built-in backdrops. */
    unsigned int surface_alpha,edge_alpha,shadow_strength;
    bool bento;
} Theme;

#define THEME_COUNT 3
static const Theme themes[THEME_COUNT]={
    {.code="graphite",.name="Graphite",.bg=0x0e1013u,.panel=0x191c21u,.panel2=0x282c34u,.line=0x2f343cu,.edge=0x24282fu,.text=0xf4f5f7u,.muted=0xa0a8b5u,
     .accent=0xff9a4bu,.container=0x3b2617u,.container_edge=0x5a3a22u,.on_accent=0x2b1605u,.icon_fill=0x302219u,.knob=0xf4f5f7u,
     .green=0x79d7aeu,.red=0xff8b8bu,.blue=0x99c8ffu,.purple=0xc9a3ffu,.danger=0x302326u,.toast_fill=0x2c313au,.toast_text=0xf4f5f7u,.shadow=0,
     .hero_top=0x3b2617u,.hero_bottom=0x3b2617u,.hero_text=0xf4f5f7u,.hero_sub=0xd8c3b4u,
     .tile_fill={0x302219u,0x302219u,0x302219u,0x302219u,0x302219u,0x302219u},.tile_ink={0xff9a4bu,0xff9a4bu,0xff9a4bu,0xff9a4bu,0xff9a4bu,0xff9a4bu},
     .terminal_bg=0x0b0e13u,.terminal_fg=0xe5e9efu,.terminal_palette=NULL,.backdrop=NULL,.surface_alpha=255,.edge_alpha=255,.shadow_strength=0,.bento=false},
    {.code="daylight",.name="Daylight",.bg=0xf7f4fdu,.panel=0xfefdffu,.panel2=0xd6dcffu,.line=0xd5d5ebu,.edge=0xffffffu,.text=0x1c1c2eu,.muted=0x58586eu,
     .accent=0x4a41e4u,.container=0xdde4ffu,.container_edge=0xc6d0fau,.on_accent=0xffffffu,.icon_fill=0xdbe3ffu,.knob=0xffffffu,
     .green=0x007948u,.red=0xc51d28u,.blue=0x0060c1u,.purple=0x8632b6u,.danger=0xffe3e4u,.toast_fill=0x1c1c2eu,.toast_text=0xffffffu,.shadow=0x3a2f7au,
     .hero_top=0x6f4ef5u,.hero_bottom=0x283ddeu,.hero_text=0xffffffu,.hero_sub=0xe4e1ffu,
     .tile_fill={0xcde7ffu,0xffe4b3u,0xbdf3dau,0xeedbffu,0xbaf0f6u,0xffd6d9u},.tile_ink={0x085dc7u,0xa76100u,0x007852u,0x7a31cau,0x00717fu,0xc81f40u},
     .terminal_bg=0xfbfaffu,.terminal_fg=0x1c1c2eu,.terminal_palette=terminal_light_palette,.backdrop=backdrop_daylight,.surface_alpha=176,.edge_alpha=150,.shadow_strength=46,.bento=true},
    {.code="aurora",.name="Aurora",.bg=0x080f1fu,.panel=0x1f283du,.panel2=0x2b3e57u,.line=0x404e63u,.edge=0x5b7088u,.text=0xf0f6fcu,.muted=0xa9b9cau,
     .accent=0x4ae6d6u,.container=0x005455u,.container_edge=0x0a6f6fu,.on_accent=0x001c1du,.icon_fill=0x004d51u,.knob=0xf0f6fcu,
     .green=0x6ce5b5u,.red=0xfd8a8cu,.blue=0x8cc3fcu,.purple=0xd0aafcu,.danger=0x4a2328u,.toast_fill=0xe8f4f6u,.toast_text=0x0b1220u,.shadow=0,
     .hero_top=0x008781u,.hero_bottom=0x373ba7u,.hero_text=0xffffffu,.hero_sub=0xc9f2eeu,
     .tile_fill={0x23436cu,0x5b4315u,0x134e39u,0x4e3468u,0x0d4b51u,0x703138u},.tile_ink={0xa6d1ffu,0xf5cf7du,0x7deabdu,0xdbb8ffu,0x79e5ebu,0xffadb3u},
     .terminal_bg=0x0b0e13u,.terminal_fg=0xe5e9efu,.terminal_palette=NULL,.backdrop=backdrop_aurora,.surface_alpha=150,.edge_alpha=70,.shadow_strength=90,.bento=true},
};
static const Theme *ui_theme=&themes[0];
/* Terminal colors are chosen apart from the interface theme. The first entry follows it.
 * Palette order: black, red, green, yellow, blue, magenta, cyan, white, then the bright set. */
typedef struct { const char *code,*name;bool follow_theme;uint32_t bg,fg,palette[16]; } TerminalScheme;
#define TERMINAL_SCHEME_COUNT 10
static const TerminalScheme terminal_schemes[TERMINAL_SCHEME_COUNT]={
    {.code="auto",.name="Match the theme",.follow_theme=true},
    {.code="catppuccin-mocha",.name="Catppuccin Mocha",.bg=0x1e1e2eu,.fg=0xcdd6f4u,.palette={0x45475au,0xf38ba8u,0xa6e3a1u,0xf9e2afu,0x89b4fau,0xf5c2e7u,0x94e2d5u,0xbac2deu,0x585b70u,0xf38ba8u,0xa6e3a1u,0xf9e2afu,0x89b4fau,0xf5c2e7u,0x94e2d5u,0xa6adc8u}},
    {.code="catppuccin-latte",.name="Catppuccin Latte",.bg=0xeff1f5u,.fg=0x4c4f69u,.palette={0x5c5f77u,0xd20f39u,0x40a02bu,0xc47a10u,0x1e66f5u,0xc55fb0u,0x179299u,0x6c6f85u,0x6c6f85u,0xd20f39u,0x40a02bu,0xc47a10u,0x1e66f5u,0xc55fb0u,0x179299u,0x4c4f69u}},
    {.code="dracula",.name="Dracula",.bg=0x282a36u,.fg=0xf8f8f2u,.palette={0x21222cu,0xff5555u,0x50fa7bu,0xf1fa8cu,0xbd93f9u,0xff79c6u,0x8be9fdu,0xf8f8f2u,0x6272a4u,0xff6e6eu,0x69ff94u,0xffffa5u,0xd6acffu,0xff92dfu,0xa4ffffu,0xffffffu}},
    {.code="nord",.name="Nord",.bg=0x2e3440u,.fg=0xd8dee9u,.palette={0x3b4252u,0xbf616au,0xa3be8cu,0xebcb8bu,0x81a1c1u,0xb48eadu,0x88c0d0u,0xe5e9f0u,0x4c566au,0xbf616au,0xa3be8cu,0xebcb8bu,0x81a1c1u,0xb48eadu,0x8fbcbbu,0xeceff4u}},
    {.code="tokyo-night",.name="Tokyo Night",.bg=0x1a1b26u,.fg=0xc0caf5u,.palette={0x15161eu,0xf7768eu,0x9ece6au,0xe0af68u,0x7aa2f7u,0xbb9af7u,0x7dcfffu,0xa9b1d6u,0x414868u,0xf7768eu,0x9ece6au,0xe0af68u,0x7aa2f7u,0xbb9af7u,0x7dcfffu,0xc0caf5u}},
    {.code="gruvbox-dark",.name="Gruvbox Dark",.bg=0x282828u,.fg=0xebdbb2u,.palette={0x282828u,0xcc241du,0x98971au,0xd79921u,0x458588u,0xb16286u,0x689d6au,0xa89984u,0x928374u,0xfb4934u,0xb8bb26u,0xfabd2fu,0x83a598u,0xd3869bu,0x8ec07cu,0xebdbb2u}},
    {.code="gruvbox-light",.name="Gruvbox Light",.bg=0xfbf1c7u,.fg=0x3c3836u,.palette={0x3c3836u,0xcc241du,0x79740eu,0xb57614u,0x076678u,0x8f3f71u,0x427b58u,0x7c6f64u,0x928374u,0x9d0006u,0x79740eu,0xb57614u,0x076678u,0x8f3f71u,0x427b58u,0x3c3836u}},
    {.code="solarized-dark",.name="Solarized Dark",.bg=0x002b36u,.fg=0x93a1a1u,.palette={0x073642u,0xdc322fu,0x859900u,0xb58900u,0x268bd2u,0xd33682u,0x2aa198u,0xeee8d5u,0x586e75u,0xcb4b16u,0x859900u,0xb58900u,0x268bd2u,0x6c71c4u,0x2aa198u,0xfdf6e3u}},
    {.code="solarized-light",.name="Solarized Light",.bg=0xfdf6e3u,.fg=0x586e75u,.palette={0x073642u,0xdc322fu,0x738a00u,0x9c7500u,0x268bd2u,0xd33682u,0x1f8a82u,0x586e75u,0x586e75u,0xcb4b16u,0x738a00u,0x9c7500u,0x268bd2u,0x6c71c4u,0x1f8a82u,0x002b36u}},
};
static const TerminalScheme *ui_terminal=&terminal_schemes[0];
static inline uint32_t terminal_scheme_bg(void){return ui_terminal->follow_theme?ui_theme->terminal_bg:ui_terminal->bg;}
static inline uint32_t terminal_scheme_fg(void){return ui_terminal->follow_theme?ui_theme->terminal_fg:ui_terminal->fg;}
static inline const uint32_t*terminal_scheme_palette(void){return ui_terminal->follow_theme?ui_theme->terminal_palette:ui_terminal->palette;}
#define BG (ui_theme->bg)
#define PANEL (ui_theme->panel)
#define PANEL2 (ui_theme->panel2)
#define LINE (ui_theme->line)
#define EDGE (ui_theme->edge)
#define TEXT (ui_theme->text)
#define MUTED (ui_theme->muted)
#define ORANGE (ui_theme->accent)
#define ORANGE_DARK (ui_theme->container)
#define ORANGE_EDGE (ui_theme->container_edge)
#define ON_ORANGE (ui_theme->on_accent)
#define ICON_FILL (ui_theme->icon_fill)
#define GREEN (ui_theme->green)
#define RED (ui_theme->red)
#define FILE_BLUE (ui_theme->blue)
#define LINK_PURPLE (ui_theme->purple)
#define TOAST_FILL (ui_theme->toast_fill)
#define TOAST_TEXT (ui_theme->toast_text)
#endif
