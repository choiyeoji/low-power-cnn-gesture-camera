`timescale 1ns / 1ps

// 4x4 tile buffer for the scheduling order:
//   four 3x3 convolutions -> one 2x2 max-pooling result.
//
// Input pixels are accepted in 4x4 row-major order:
//   0  1  2  3
//   4  5  6  7
//   8  9 10 11
//  12 13 14 15
//
// Windows are emitted as soon as enough pixels of the current 4x4 tile are
// present. With full tiles this starts after the 11th pixel; with horizontal
// or vertical reuse it starts earlier because half of the tile is already
// cached.
//   window_index=0 : top-left convolution position
//   window_index=1 : top-right convolution position
//   window_index=2 : bottom-left convolution position
//   window_index=3 : bottom-right convolution position
//
// MaxPool can therefore collect four completed CH results in this same order.
module Shift_Buffer #(
    parameter integer PIXEL_WIDTH = 8,
    parameter integer TILE_X_WIDTH = 8,
    parameter integer MAX_TILE_COLUMNS = 128
) (
    input logic clk,
    input logic rst_n,

    // Clears a partially captured tile and returns to input capture.
    input logic clear,
    input logic reuse_horizontal,
    input logic reuse_vertical,
    input logic [TILE_X_WIDTH-1:0] tile_x_index,

    // Data_Buffer input stream. A pixel is accepted only on
    // pixel_valid && pixel_ready.
    input  logic signed [PIXEL_WIDTH-1:0] pixel_data,
    input  logic                   pixel_valid,
    output logic                   pixel_ready,

    // CH_wrapper interface.
    output logic signed [PIXEL_WIDTH-1:0] pixel_window [0:8],
    output logic                   window_valid,
    input  logic                   window_ready,
    output logic [1:0]             window_index,

    // High together with the fourth and final window.
    output logic tile_done
);
    localparam integer TILE_PIXELS = 16;

    logic signed [PIXEL_WIDTH-1:0] tile_mem [0:TILE_PIXELS-1];
    logic signed [PIXEL_WIDTH-1:0] vertical_cache [0:MAX_TILE_COLUMNS-1][0:7];
    logic [4:0] capture_count;
    logic [2:0] available_window_count;
    logic [2:0] consumed_window_count;
    logic       pixel_fire;
    logic       window_fire;
    logic [4:0] target_capture_count;
    logic       cache_commit_pending;
    logic [TILE_X_WIDTH-1:0] cache_commit_tile_x;

    integer index;
    integer load_cache_index;
    integer cache_write_index;
    integer cache_init_index;

    assign pixel_ready = (capture_count < target_capture_count);
    assign pixel_fire  = pixel_valid && pixel_ready;
    assign window_fire = window_valid && window_ready;

    assign window_valid = (consumed_window_count < available_window_count);
    assign window_index = consumed_window_count[1:0];
    assign tile_done = window_fire && (consumed_window_count == 3);

    always_comb begin
        if (reuse_horizontal || reuse_vertical)
            target_capture_count = 5'd8;
        else
            target_capture_count = 5'd16;
    end

    // Select one of the four overlapping 3x3 windows from the stored 4x4 tile.
    always_comb begin
        for (index = 0; index < 9; index = index + 1)
            pixel_window[index] = {PIXEL_WIDTH{1'b0}};

        case (consumed_window_count[1:0])
            2'd0: begin
                pixel_window[0] = tile_mem[0];
                pixel_window[1] = tile_mem[1];
                pixel_window[2] = tile_mem[2];
                pixel_window[3] = tile_mem[4];
                pixel_window[4] = tile_mem[5];
                pixel_window[5] = tile_mem[6];
                pixel_window[6] = tile_mem[8];
                pixel_window[7] = tile_mem[9];
                pixel_window[8] = tile_mem[10];
            end

            2'd1: begin
                pixel_window[0] = tile_mem[1];
                pixel_window[1] = tile_mem[2];
                pixel_window[2] = tile_mem[3];
                pixel_window[3] = tile_mem[5];
                pixel_window[4] = tile_mem[6];
                pixel_window[5] = tile_mem[7];
                pixel_window[6] = tile_mem[9];
                pixel_window[7] = tile_mem[10];
                pixel_window[8] = tile_mem[11];
            end

            2'd2: begin
                pixel_window[0] = tile_mem[4];
                pixel_window[1] = tile_mem[5];
                pixel_window[2] = tile_mem[6];
                pixel_window[3] = tile_mem[8];
                pixel_window[4] = tile_mem[9];
                pixel_window[5] = tile_mem[10];
                pixel_window[6] = tile_mem[12];
                pixel_window[7] = tile_mem[13];
                pixel_window[8] = tile_mem[14];
            end

            default: begin
                pixel_window[0] = tile_mem[5];
                pixel_window[1] = tile_mem[6];
                pixel_window[2] = tile_mem[7];
                pixel_window[3] = tile_mem[9];
                pixel_window[4] = tile_mem[10];
                pixel_window[5] = tile_mem[11];
                pixel_window[6] = tile_mem[13];
                pixel_window[7] = tile_mem[14];
                pixel_window[8] = tile_mem[15];
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            capture_count <= 5'd0;
            available_window_count <= 3'd0;
            consumed_window_count  <= 3'd0;
            cache_commit_pending <= 1'b0;
            cache_commit_tile_x <= '0;
        end else if (clear) begin
            capture_count <= 5'd0;
            available_window_count <= 3'd0;
            consumed_window_count  <= 3'd0;
            cache_commit_pending <= 1'b0;
        end else begin
            cache_commit_pending <= 1'b0;

            if (pixel_fire) begin
                if (reuse_horizontal) begin
                    if (capture_count == 0) begin
                        tile_mem[0]  <= tile_mem[2];
                        tile_mem[1]  <= tile_mem[3];
                        tile_mem[4]  <= tile_mem[6];
                        tile_mem[5]  <= tile_mem[7];
                        tile_mem[8]  <= tile_mem[10];
                        tile_mem[9]  <= tile_mem[11];
                        tile_mem[12] <= tile_mem[14];
                        tile_mem[13] <= tile_mem[15];
                    end
                    tile_mem[((capture_count >> 1) << 2)
                             + 2 + capture_count[0]] <= pixel_data;
                end else if (reuse_vertical) begin
                    if (capture_count == 0) begin
                        for (load_cache_index = 0; load_cache_index < 8;
                             load_cache_index = load_cache_index + 1)
                            tile_mem[load_cache_index] <=
                                vertical_cache[tile_x_index][load_cache_index];
                    end
                    tile_mem[8 + capture_count] <= pixel_data;
                end else begin
                    tile_mem[capture_count] <= pixel_data;
                end

                capture_count <= capture_count + 1'b1;

                if (!reuse_horizontal && !reuse_vertical) begin
                    case (capture_count)
                        5'd10: available_window_count <= 3'd1;
                        5'd11: available_window_count <= 3'd2;
                        5'd14: available_window_count <= 3'd3;
                        5'd15: available_window_count <= 3'd4;
                        default: begin end
                    endcase
                end else if (reuse_horizontal) begin
                    case (capture_count)
                        5'd4: available_window_count <= 3'd1;
                        5'd5: available_window_count <= 3'd2;
                        5'd6: available_window_count <= 3'd3;
                        5'd7: available_window_count <= 3'd4;
                        default: begin end
                    endcase
                end else begin
                    case (capture_count)
                        5'd2: available_window_count <= 3'd1;
                        5'd3: available_window_count <= 3'd2;
                        5'd6: available_window_count <= 3'd3;
                        5'd7: available_window_count <= 3'd4;
                        default: begin end
                    endcase
                end
            end

            if (window_fire) begin
                if (consumed_window_count == 3) begin
                    // Stage cache write enable to cut long CE timing path.
                    cache_commit_pending <= 1'b1;
                    cache_commit_tile_x <= tile_x_index;
                    capture_count <= 5'd0;
                    available_window_count <= 3'd0;
                    consumed_window_count <= 3'd0;
                end else begin
                    consumed_window_count <= consumed_window_count + 1'b1;
                end
            end
        end
    end

    // Keep vertical cache updates in a dedicated register block so clear does
    // not drive the CE of the full cache bank.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (cache_init_index = 0;
                cache_init_index < MAX_TILE_COLUMNS;
                cache_init_index = cache_init_index + 1)
                for (cache_write_index = 0; cache_write_index < 8;
                    cache_write_index = cache_write_index + 1)
                    vertical_cache[cache_init_index][cache_write_index] <= '0;
        end else if (cache_commit_pending) begin
            for (cache_write_index = 0; cache_write_index < 8;
                cache_write_index = cache_write_index + 1)
                vertical_cache[cache_commit_tile_x][cache_write_index] <=
                    tile_mem[8 + cache_write_index];
        end
    end

endmodule
